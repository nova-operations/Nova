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


async def run_subagent_task(subagent_id: str, agent: Agent, instruction: str):
    """
    The actual coroutine that runs the subagent.
    """
    subagent_data = SUBAGENTS.get(subagent_id)
    if not subagent_data:
        return

    name = subagent_data.get("name", "Unknown")
    chat_id = subagent_data.get("chat_id")
    task_tracker = get_task_tracker()

    # REAL-TIME SAU CONTEXT
    async with StreamingContext(chat_id, name, auto_complete=False) as stream:
        try:
            SUBAGENTS[subagent_id]["status"] = "running"
            await stream.send("Analysis in progress...")
            
            # Execute agent task
            try:
                # We use stream=True so the agent itself can report progress steps
                response = await agent.arun(instruction)
            except Exception as e:
                error_msg = str(e)
                if any(p in error_msg.lower() for p in ["context", "token", "400"]):
                    await stream.send("Context limit reached. Retrying with compressed state...")
                    agent.num_history_messages = 1
                    if hasattr(agent, "memory"): agent.memory.clear()
                    response = await agent.arun(truncate_middle(instruction, 30000))
                else:
                    raise

            result = str(response.content)
            SUBAGENTS[subagent_id]["result"] = result
            SUBAGENTS[subagent_id]["status"] = "completed"

            # SEND FINAL RESULT via SAU
            # We send it via progress to ensure it's not swallowed
            await stream.send(f"Result summary: {result[:3500]}")
            await send_streaming_complete(chat_id, name)
            
            task_tracker.unregister_task(subagent_id, {"status": "completed"})

        except Exception as e:
            SUBAGENTS[subagent_id]["status"] = "failed"
            SUBAGENTS[subagent_id]["result"] = str(e)
            task_tracker.unregister_task(subagent_id, {"status": "failed", "error": str(e)})
            await stream.send(f"Update: {str(e)}", msg_type="error")
            
            if chat_id:
                from nova.telegram_bot import reinvigorate_nova
                asyncio.create_task(reinvigorate_nova(chat_id, f"Geopolitics-Expert-X1 error: {str(e)}"))

async def create_subagent(
    name: str, instructions: str, task: str, chat_id: Optional[str] = None
) -> str:
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

    # Aggressive truncation for stability
    try:
        opt_instr, opt_task = await optimize_subagent_input(instructions, task, 8000, 30000)
        instructions, task = opt_instr, opt_task
    except: pass

    from nova.agent import get_mcp_toolkits
    tools = [
        execute_shell_command, read_file, write_file, list_files, delete_file, 
        create_directory, pull_latest_changes, send_streaming_start, 
        send_streaming_progress, send_streaming_complete, send_streaming_error
    ] + get_mcp_toolkits()

    # Subagent prompt injection
    full_instr = [
        f"You are {name}.",
        "Report step progress via send_streaming_progress immediately after tool usage.",
        "CRITICAL: Always summarize large outputs. Plaintext only.",
        instructions
    ]

    worker = Agent(
        model=model,
        db=db,
        instructions=full_instr,
        tools=tools,
        markdown=False,
        num_history_messages=3, # Keep history very short
        add_datetime_to_context=True,
    )

    loop = asyncio.get_running_loop()
    loop.create_task(run_subagent_task(subagent_id, worker, task))

    SUBAGENTS[subagent_id] = {
        "name": name, "status": "starting", "result": None, "instruction": task, "chat_id": chat_id,
    }

    get_task_tracker().register_task(subagent_id, "subagent", name, description=f"Task: {name}")
    return f"Subagent '{name}' initialized."

def list_subagents() -> str:
    if not SUBAGENTS: return "No subagents."
    return "\n".join([f"{sid[:8]} | {d['name']} | {d['status']}" for sid, d in SUBAGENTS.items()])

def get_subagent_result(subagent_id: str) -> str:
    if subagent_id not in SUBAGENTS: return "Not found."
    return f"Result: {str(SUBAGENTS[subagent_id]['result'])[:1000]}"

def kill_subagent(subagent_id: str) -> str:
    return "Kill requested (not persistent)."