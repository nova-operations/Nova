import asyncio
import uuid
import logging
from typing import Dict, Optional, List
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from dotenv import load_dotenv
import os

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

load_dotenv()

# Global dictionary to store running subagents
SUBAGENTS: Dict[str, Dict] = {}


def get_telegram_bot():
    """Get the Telegram bot instance, trying multiple sources."""
    # Use the function from streaming_utils
    return _get_telegram_bot()


async def run_subagent_task(subagent_id: str, agent: Agent, instruction: str):
    """
    The actual coroutine that runs the subagent.
    Uses SAU (Subagent Automatic Updates) for real-time progress reporting.
    
    IMPORTANT: Now operates in BATCHED mode - only sends START and COMPLETE messages.
    """
    subagent_data = SUBAGENTS.get(subagent_id)
    if not subagent_data:
        logging.error(f"Subagent {subagent_id} not found in SUBAGENTS dict")
        return

    name = subagent_data.get("name", "Unknown")
    chat_id = subagent_data.get("chat_id")

    # Create streaming context for SAU updates (batched mode)
    # This will send START on entry and COMPLETE on exit automatically
    async with StreamingContext(chat_id, name, auto_complete=False) as stream:
        try:
            SUBAGENTS[subagent_id]["status"] = "running"
            
            # Log - but don't send individual messages (batched)
            logging.info(
                f"Subagent {subagent_id} started running: {instruction[:200]}..."
            )

            # Send progress through the context (will be batched, not sent individually)
            await stream.send("Initializing agent and processing task...")
            
            # Run the agent asynchronously
            await stream.send("Executing task with AI model...")
            response = await agent.arun(instruction)

            # Process result
            await stream.send("Processing results...")
            result = response.content

            SUBAGENTS[subagent_id]["result"] = result
            SUBAGENTS[subagent_id]["status"] = "completed"

            logging.info(f"Subagent {subagent_id} completed.")
            
            # Send completion notification - the StreamingContext will handle this
            # on __aexit__ with the summary from batched progress messages

        except Exception as e:
            SUBAGENTS[subagent_id]["status"] = "failed"
            SUBAGENTS[subagent_id]["result"] = str(e)

            # Send error notification via SAU
            await stream.send(f"Task failed: {str(e)}", msg_type="error")
            logging.error(f"Subagent {subagent_id} failed: {e}")

    # After completion, send final notification to user if chat_id is available
    # The StreamingContext handles the main completion message
    if subagent_data and subagent_data.get("chat_id"):
        final_chat_id = subagent_data["chat_id"]
        final_name = subagent_data["name"]
        status = subagent_data["status"]
        result = subagent_data["result"]

        # Get the telegram bot instance using our helper
        telegram_bot = get_telegram_bot()

        if telegram_bot:
            status_emoji = "DONE" if status == "completed" else "FAILED"
            
            # Build the completion message - plaintext only
            # Note: StreamingContext already sent completion, this is optional detail
            if status == "completed" and result:
                # Strip all formatting from result
                clean_result = strip_all_formatting(str(result))
                msg = f"{status_emoji} Subagent '{final_name}' completed!\n\nResult:\n{clean_result}"
            else:
                clean_error = strip_all_formatting(str(result))
                msg = f"{status_emoji} Subagent '{final_name}' failed!\n\nError: {clean_error}"

            # Use long message handler to automatically convert to PDF if needed
            await send_message_with_fallback(
                telegram_bot,
                int(final_chat_id),
                msg,
                title=f"Subagent Report: {final_name}",
            )


def _preprocess_task_with_context_optimization(task: str) -> str:
    """
    Preprocess subagent task with context optimization.

    This handles the case where tool outputs (especially web searches)
    produce massive amounts of text that would exceed context limits.

    The function:
    1. Detects if the task contains large search results or tool outputs
    2. Applies middle-out transformation or summarization
    3. Adds instructions for the subagent to summarize results before final output

    Args:
        task: The raw task string

    Returns:
        Optimized task string with context management instructions
    """
    # If task is very large, optimize it before passing to agent
    if len(task) > CHAR_LIMIT_HIGH:
        optimizer = get_context_optimizer()

        # Use middle-out for moderately large, summarize for very large
        if len(task) > 200000:
            # Use synchronous fallback for emergency truncation
            optimized_task = optimizer._middle_out_transform(task, CHAR_LIMIT_EMERGENCY)
            logging.warning(
                f"Emergency truncation applied: {len(task)} -> {len(optimized_task)} chars"
            )
        else:
            # Apply middle-out transformation
            optimized_task = optimizer._middle_out_transform(task, CHAR_LIMIT_HIGH)
            logging.info(
                f"Middle-out transform applied: {len(task)} -> {len(optimized_task)} chars"
            )

        # Add context management prefix
        task = f"""IMPORTANT: The following task contains large input data that has been truncated for context management.

CONTEXT LIMITATIONS:
- The input has been reduced from {len(task)} to approximately {CHAR_LIMIT_HIGH} characters
- Key sections at start, middle, and end are preserved
- You may need to work with partial data

YOUR TASK:
{optimized_task}

IMPORTANT INSTRUCTIONS:
1. Process the task with the available data
2. If you need more information, use tools to fetch only what's essential
3. When providing final results, keep them concise - avoid dumping entire tool outputs
4. If results are long, summarize key findings rather than including all raw data
5. Use bullet points and keep your response under 2000 words if possible

Begin task execution now.
"""

    return task


# Changed to async def to ensure we are in a valid async context
async def create_subagent(
    name: str, instructions: str, task: str, chat_id: Optional[str] = None
) -> str:
    """
    Creates and starts a subagent in the background.
    Uses SAU (Subagent Automatic Updates) as the default reporting mechanism.

    Args:
        name: A name for the subagent.
        instructions: Instructions for the subagent's persona/behavior.
        task: The specific task or question for the subagent to process.
        chat_id: The Telegram Chat ID to send SAU live updates to.

    Returns:
        The ID of the created subagent.
    """
    subagent_id = str(uuid.uuid4())

    # Configure the model
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "Error: OPENROUTER_API_KEY not set."

    subagent_model = os.getenv("SUBAGENT_MODEL", "google/gemini-2.0-flash-001")

    model = OpenAIChat(
        id=subagent_model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    database_url = os.getenv("DATABASE_URL")
    if database_url and (
        database_url.startswith("postgresql://")
        or database_url.startswith("postgres://")
    ):
        # Ensure the scheme is postgresql:// for SQLAlchemy compatibility
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        db = PostgresDb(session_table="nova_subagent_sessions", db_url=database_url)
    else:
        # Use persistent path for DB if available (e.g. Railway volume at /app/data)
        # If /app/data doesn't exist, fallback to local dir
        db_path = "/app/data/nova_memory.db"
        if not os.path.exists("/app/data"):
            try:
                os.makedirs("/app/data", exist_ok=True)
            except OSError:
                # Fallback if we can't create directory
                db_path = "nova_memory.db"
        db = SqliteDb(db_file=db_path)

    # Safeguard: Log lengths
    instr_len = len(instructions)
    task_len = len(task)
    logging.info(
        f"Creating subagent '{name}' (instr_len: {instr_len}, task_len: {task_len})"
    )

    # Apply context optimization to instructions and task
    # This is the key fix for the token overflow issue
    try:
        optimized_instructions, optimized_task = await optimize_subagent_input(
            instructions=instructions,
            task=task,
            max_instruction_tokens=10000,  # ~40k chars
            max_task_tokens=150000,  # ~600k chars (conservative for subagent)
        )
        instructions = optimized_instructions
        task = optimized_task
        logging.info(f"Context optimization applied for subagent '{name}'")
    except Exception as e:
        # Fallback to basic truncation if optimization fails
        logging.warning(f"Context optimization failed: {e}, using basic truncation")

        if instr_len > 50000:
            instructions = instructions[:50000] + "\n\n... [TRUNCATED] ..."

        if task_len > 50000:
            task = _preprocess_task_with_context_optimization(task)

    # Give subagents DOER tools, avoid giving management tools to prevent recursion
    tools_list = [
        execute_shell_command,
        read_file,
        write_file,
        list_files,
        delete_file,
        create_directory,
        push_to_github,
        pull_latest_changes,
        send_streaming_start,
        send_streaming_progress,
        send_streaming_complete,
        send_streaming_error,
    ]

    # Add MCP tools (Search, etc.)
    try:
        from nova.agent import get_mcp_toolkits

        tools_list.extend(get_mcp_toolkits())
    except ImportError:
        logging.warning("Could not import get_mcp_toolkits in subagent")

    # MANDATORY SAU INSTRUCTIONS - These are injected into every subagent's system prompt
    # This ensures all subagents report milestones via SAU by default
    sau_instructions = """## MANDATORY LIVE UPDATES (SAU) - DEFAULT BEHAVIOR:
- YOU MUST use the streaming system to report milestones IMMEDIATELY as you progress.
- Use the `send_streaming_start`, `send_streaming_progress`, and `send_streaming_complete` functions.
- The header format for all updates is: [SAU: {agent_name}]
- Report at key milestones: initialization, tool execution, results processing, completion.
- NEVER wait for completion to send updates - report progress in real-time.
- If errors occur, use `send_streaming_error` immediately.
- This is NOT optional - it is the MANDATORY default for all subagent reporting.
- Legacy heartbeat/PM polling is DISABLED for your tasks.
"""

    # Enhanced instructions with MANDATORY SAU
    enhanced_instructions = [
        f"You are a specialized subagent named '{name}'.",
        "Your goal is to execute the specific task assigned to you.",
        "You have access to shell, filesystem, and specialized MCP tools.",
        "Focus on DOING the work rather than delegating.",
        "",
        sau_instructions,
        "# CRITICAL: NO MARKDOWN - Telegram renders all markdown as plain text.",
        "# NEVER use: **bold**, *italic*, # headers, `code`, - or * bullet lists.",
        "# Always output clean plaintext that displays correctly in Telegram.",
        "",
        "## CONTEXT MANAGEMENT (IMPORTANT):",
        "- You may receive truncated or summarized inputs due to context limits",
        "- Do NOT ask for more data - work with what you have",
        "- When presenting results, SUMMARIZE rather than dumping raw data",
        "- Use bullet points and keep responses concise",
        "- If results exceed 2000 words, provide an executive summary",
        "",
        instructions,
    ]

    # FIX: Removed num_history_runs to avoid Agno warning about setting both
    # num_history_messages and num_history_runs at the same time.
    # Using num_history_messages=10 only to keep last 10 messages for context.
    agent = Agent(
        model=model,
        db=db,
        description=f"Subagent {name} - A specialized worker focused on execution.",
        instructions=enhanced_instructions,
        tools=tools_list,
        markdown=False,
        add_history_to_context=True,
        num_history_messages=10,  # Only keep last 10 messages for context
        # NOTE: Removed num_history_runs to fix Agno warning
        add_datetime_to_context=True,  # Helpful for news/search
    )

    # Create the task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return "Error: No running event loop found. Subagents must be created within an async context."

    t = loop.create_task(run_subagent_task(subagent_id, agent, task))

    SUBAGENTS[subagent_id] = {
        "name": name,
        "task_obj": t,
        "agent": agent,
        "status": "starting",
        "result": None,
        "instruction": task,
        "chat_id": chat_id,
    }

    # Heartbeat monitoring DISABLED - SAU is the mandatory default
    logging.info(
        f"Subagent '{name}' created - SAU live updates enabled (heartbeat disabled)"
    )

    # REMOVED: Duplicate notification sending
    # The StreamingContext in run_subagent_task will automatically send
    # START and COMPLETE messages, so we don't need additional notifications here.
    # This was causing double/triple messages before.

    return f"Subagent '{name}' created with ID: {subagent_id}"


def list_subagents() -> str:
    """Lists all managed subagents and their status."""
    if not SUBAGENTS:
        return "No subagents found."

    report = []
    for sid, data in SUBAGENTS.items():
        status = data["status"]
        name = data["name"]
        report.append(f"ID: {sid} | Name: {name} | Status: {status}")
    return "\n".join(report)


def get_subagent_result(subagent_id: str) -> str:
    """
    Retrieves the result of a subagent.
    If the subagent is still running, it returns the current status.
    The caller should poll this tool until 'completed' or 'failed' is returned.
    """
    if subagent_id not in SUBAGENTS:
        return "Error: Subagent not found."

    data = SUBAGENTS[subagent_id]
    if data["status"] == "completed":
        result = data["result"]
        # Check if result is too long and provide info about PDF conversion
        if is_message_too_long(str(result)):
            return (
                f"Result for {data['name']}:\n\n"
                f"[Result is {len(str(result))} chars, exceeds Telegram limit of {TELEGRAM_MAX_LENGTH}. "
                f"It was sent as a PDF via the notification system.]"
            )
        return f"Result for {data['name']}:\n{data['result']}"
    elif data["status"] == "failed":
        return f"Subagent {data['name']} failed: {data['result']}"
    else:
        return f"Subagent {data['name']} is currently {data['status']}."


def kill_subagent(subagent_id: str) -> str:
    """Stops a running subagent."""
    if subagent_id not in SUBAGENTS:
        return "Error: Subagent not found."

    data = SUBAGENTS[subagent_id]
    if data["status"] in ["running", "starting"]:
        data["task_obj"].cancel()
        data["status"] = "cancelled"

        # Send cancellation notification via SAU
        chat_id = data.get("chat_id")
        name = data.get("name")
        if chat_id:
            asyncio.create_task(
                send_streaming_error(chat_id, name, "Task was cancelled by user")
            )

        return f"Subagent {data['name']} cancelled."
    else:
        return f"Subagent {data['name']} is not running (Status: {data['status']})."