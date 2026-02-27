import asyncio
import uuid
import logging
import threading
from typing import Dict, Optional, List, Any
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from dotenv import load_dotenv
import os
import re

# Import all tools to give to subagents
from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import (
    read_file,
    write_file,
    list_files,
    delete_file,
    create_directory,
)
from nova.tools.github_tools import push_to_github, pull_latest_changes

# Import long message handler for PDF conversion
from nova.long_message_handler import (
    send_message_with_fallback,
    is_message_too_long,
    create_pdf_from_text,
    process_long_message,
    TELEGRAM_MAX_LENGTH,
)

# Import context optimizer for token management
from nova.tools.context_optimizer import (
    optimize_subagent_input,
    optimize_search_results,
    get_context_optimizer,
    CHAR_LIMIT_HIGH,
    CHAR_LIMIT_EMERGENCY,
    truncate_middle
)

# Import streaming utilities for real-time updates
from nova.tools.streaming_utils import (
    _get_telegram_bot,
    send_streaming_start,
    send_streaming_progress,
    send_streaming_complete,
    send_streaming_error,
    StreamingContext,
    strip_all_formatting,
)

# Import task tracker for deployment locking integration
from nova.task_tracker import TaskTracker

load_dotenv()

# Global dictionary to store running subagents
SUBAGENTS: Dict[str, Dict] = {}

# Global task tracker instance
_task_tracker: Optional[TaskTracker] = None


def get_task_tracker() -> TaskTracker:
    """Get or create the global task tracker instance."""
    global _task_tracker
    if _task_tracker is None:
        _task_tracker = TaskTracker()
    return _task_tracker


def get_telegram_bot():
    """Get the Telegram bot instance, trying multiple sources."""
    # Use the function from streaming_utils
    return _get_telegram_bot()


async def run_subagent_task(subagent_id: str, agent: Agent, instruction: str):
    """
    The actual coroutine that runs the subagent.
    Uses SAU (Subagent Automatic Updates) for real-time progress reporting.
    """
    subagent_data = SUBAGENTS.get(subagent_id)
    if not subagent_data:
        logging.error(f"Subagent {subagent_id} not found in SUBAGENTS dict")
        return

    name = subagent_data.get("name", "Unknown")
    chat_id = subagent_data.get("chat_id")
    task_tracker = get_task_tracker()

    async with StreamingContext(chat_id, name, auto_complete=False) as stream:
        try:
            SUBAGENTS[subagent_id]["status"] = "running"
            logging.info(f"Subagent {subagent_id} started running.")

            await stream.send("Analysis in progress...")
            
            # Wrap internal arun with error handling for context length
            try:
                response = await agent.arun(instruction)
            except Exception as e:
                error_msg = str(e)
                if "maximum context length" in error_msg.lower() or "400" in error_msg:
                    logging.warning(f"Context error in subagent {name}: {error_msg}. Retrying with compressed history.")
                    await stream.send("Optimizing context and retrying...")
                    
                    # Force history reduction
                    agent.num_history_messages = 2
                    if hasattr(agent, "memory"):
                        agent.memory.clear()
                    
                    # Truncate instruction as last resort
                    instruction = truncate_middle(instruction, 50000)
                    response = await agent.arun(instruction)
                else:
                    raise

            result = response.content
            SUBAGENTS[subagent_id]["result"] = result
            SUBAGENTS[subagent_id]["status"] = "completed"

            task_tracker.unregister_task(subagent_id, {"status": "completed"})

        except Exception as e:
            SUBAGENTS[subagent_id]["status"] = "failed"
            SUBAGENTS[subagent_id]["result"] = str(e)
            task_tracker.unregister_task(subagent_id, {"status": "failed", "error": str(e)})
            await stream.send(f"Status alert: {str(e)}", msg_type="error")
            
            if chat_id:
                from nova.telegram_bot import reinvigorate_nova
                asyncio.create_task(reinvigorate_nova(chat_id, f"Subagent '{name}' encountered a critical error: {str(e)}"))

async def create_subagent(
    name: str, instructions: str, task: str, chat_id: Optional[str] = None
) -> str:
    """
    Creates and starts a subagent in the background.
    """
    subagent_id = str(uuid.uuid4())
    api_key = os.getenv("OPENROUTER_API_KEY")
    subagent_model = os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5")

    model = OpenAIChat(id=subagent_model, api_key=api_key, base_url="https://openrouter.ai/api/v1")

    database_url = os.getenv("DATABASE_URL")
    if database_url and ("postgresql://" in database_url or "postgres://" in database_url):
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        db = PostgresDb(session_table="nova_subagent_sessions", db_url=database_url)
    else:
        db = SqliteDb(db_file="/app/data/nova_memory.db" if os.path.exists("/app/data") else "nova_memory.db")

    # Apply aggressive context optimization to inputs
    try:
        opt_instructions, opt_task = await optimize_subagent_input(
            instructions=instructions,
            task=task,
            max_instruction_tokens=8000,
            max_task_tokens=40000 
        )
        instructions, task = opt_instructions, opt_task
    except Exception as e:
        logging.warning(f"Input optimization failed: {e}")

    # Build tools with search wrapping
    from nova.agent import get_mcp_toolkits
    base_tools = [
        execute_shell_command, read_file, write_file, list_files, delete_file, 
        create_directory, pull_latest_changes, send_streaming_start, 
        send_streaming_progress, send_streaming_complete, send_streaming_error
    ]
    
    # Wrap research results if needed
    mcp_tools = get_mcp_toolkits()
    tools_list = base_tools + mcp_tools

    enhanced_instructions = [
        f"You are {name}.",
        "Report milestones via SAU tools.",
        "CRITICAL: If tool data is too long, describe the key findings instead of dumping results.",
        "NO MARKDOWN.",
        instructions,
    ]

    agent = Agent(
        model=model,
        db=db,
        instructions=enhanced_instructions,
        tools=tools_list,
        markdown=False,
        add_history_to_context=True,
        num_history_messages=5, 
        add_datetime_to_context=True,
    )

    loop = asyncio.get_running_loop()
    t = loop.create_task(run_subagent_task(subagent_id, agent, task))

    SUBAGENTS[subagent_id] = {
        "name": name, "task_obj": t, "agent": agent, "status": "starting",
        "result": None, "instruction": task, "chat_id": chat_id,
    }

    get_task_tracker().register_task(subagent_id, "subagent", name, description=f"Subagent task: {name}")
    return f"Subagent '{name}' initialized."

def list_subagents() -> str:
    if not SUBAGENTS: return "No active subagents."
    return "\n".join([f"ID: {sid} | Name: {d['name']} | Status: {d['status']}" for sid, d in SUBAGENTS.items()])

def get_subagent_result(subagent_id: str) -> str:
    if subagent_id not in SUBAGENTS: return "Error: Not found."
    data = SUBAGENTS[subagent_id]
    return f"Status: {data['status']} | Content: {str(data['result'])[:500]}"

def kill_subagent(subagent_id: str) -> str:
    if subagent_id not in SUBAGENTS: return "Error: Not found."
    SUBAGENTS[subagent_id]["task_obj"].cancel()
    return f"Killed {SUBAGENTS[subagent_id]['name']}."