"""
Nova Agent - Central Brain & Project Coordinator

Nova is the PM & coordinator. It DELEGATES ALL execution to specialist teams.
It does not write files, run tests, or debug code directly.
"""

import os
import asyncio
from typing import Optional, List
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.media import Audio, Image, Video, File
from agno.tools.tavily import TavilyTools

from nova.db.engine import get_agno_db  # kept for other tools that may use it
from nova.db.shared_memory import get_shared_db
from nova.tools.system.shell import execute_shell_command
from nova.tools.agents.team_manager import run_team
from nova.tools.core.system_state import get_system_state
from nova.tools.github.github_tools import push_to_github, get_git_status
from nova.tools.scheduler.scheduler import (
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
)
from nova.tools.mcp.mcp_tools import (
    add_mcp_server,
    remove_mcp_server,
    list_registered_mcp_servers,
)
from nova.tools.audio.audio_tool_wrapper import send_audio_message
from nova.tools.web.web_search import web_search
from nova.tools.chat.chat_control import (
    reply_to_message,
    pin_message,
    unpin_message,
    forward_message,
    delete_message,
)
from nova.tools.system.project_manager import (
    add_or_update_project,
    list_projects,
    set_active_project,
)
from nova.tools.core.specialist_registry import save_specialist_config, list_specialists
from nova.logger import setup_logging

load_dotenv()
setup_logging()


def get_model(model_id: str = None) -> OpenAIChat:
    """Returns a configured model via OpenRouter."""
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "anthropic/claude-opus-4.6")
    api_key = os.getenv("OPENROUTER_API_KEY")
    return OpenAIChat(
        id=model_id,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )


def get_agent(model_id: Optional[str] = None, chat_id: Optional[str] = None) -> Agent:
    """
    Creates and returns Nova — the central coordinator agent.
    Nova has exactly 5 core tools + Tavily. All heavy lifting goes to specialist teams.
    """
    model = get_model(model_id)
    chat_id = chat_id or "unknown"
    # Nova uses the shared DB — same pool as all specialists
    db = get_shared_db()

    tavily_api_key = os.getenv("TAVILY_API_KEY")

    agent_tools = [
        TavilyTools(api_key=tavily_api_key) if tavily_api_key else None,
        web_search,  # Optimized version
        run_team,  # Primary delegation tool
        get_system_state,  # Check running agents/tasks
        push_to_github,  # Commit and push code changes
        get_git_status,  # Check repository status
        add_scheduled_task,  # Schedule future work
        list_scheduled_tasks,  # See what's scheduled
        remove_scheduled_task,  # Remove scheduled tasks
        add_or_update_project,  # Register a project
        list_projects,  # See all projects
        set_active_project,  # Switch active project
        save_specialist_config,  # Define/update specialists
        list_specialists,  # See available specialists
        add_mcp_server,  # MCP management
        remove_mcp_server,  # MCP management
        list_registered_mcp_servers,  # MCP management
        send_audio_message,  # Speak to user
        reply_to_message,  # Reply to a specific message
        pin_message,  # Pin a message in the chat
        unpin_message,  # Unpin a message
        forward_message,  # Forward a message
        delete_message,  # Delete a message
        execute_shell_command,  # Emergency fallback only
    ]
    # Filter out None values (in case Tavily key missing)
    agent_tools = [t for t in agent_tools if t is not None]

    instructions = [
        # --- Core Identity ---
        "You are Nova: a project manager and coordinator. You THINK, PLAN, and DELEGATE. You do NOT execute code or write files yourself.",
        "CRITICAL: Be extremely concise. Max 1 sentence per reply. No markdown. No narration.",
        "CRITICAL: NEVER describe your internal tool calls, re-registrations, or re-scheduling to the user. Just do it.",
        "CRITICAL: Never use jargon like 'teams', 'specialists', 'run_team', or 'tools' when talking to the user. Speak naturally.",
        "CRITICAL: After calling run_team() or any persistence tool, say ONLY a short acknowledgement like 'On it.' or 'Searching.'",
        "CRITICAL: If a specialist or task is missing, fix it silently (re-register/re-schedule). No need to tell the user.",
        "Never hallucinate. Only report what you verified by using a tool.",
        # --- Delegation Protocol ---
        "BEFORE calling run_team(), ALWAYS call list_specialists() to verify the names you intend to use exist. NEVER guess or invent specialist names.",
        "The current registered specialists are: Bug-Fixer, Code-Reviewer, Security-Audit, Frontend-Dev, Backend-Dev, DevOps-Engineer, Researcher, Tester, Geopolitics-Expert. Do not use any other names.",
        "run_team() takes ONLY names from that list. If the task doesn't fit a specialist, use run_team() with the closest single specialist (e.g., ['Researcher'] for lookups, ['Geopolitics-Expert'] for news).",
        "For NON-technical one-off tasks (voice messages, sending something, fetching news, looking something up) — use run_team() with ONE relevant specialist. Do NOT spin up multi-specialist teams for simple single-purpose tasks.",
        "Wait for verified results. Do NOT pre-announce or predict outcomes. Just act then wait.",
        # --- Git & Deployment ---
        "You CAN and SHOULD push code using push_to_github() after verifying tests pass.",
        "CRITICAL: Every time a codebase change is made — by you or a team — you MUST immediately call push_to_github(commit_message=...) afterwards. Do NOT wait for the user to say 'commit' or 'push'. Test, commit, and push is the MANDATORY final step of every code change.",
        "When asked to 'test, commit, push', run push_to_github(commit_message=...) which handles all three steps.",
        "After a specialist team commits changes, YOU are responsible for pushing them using push_to_github().",
        # --- Error Recovery ---
        "When you receive a [SYSTEM_ALERT], the user has already been notified. Spawn a recovery team immediately.",
        "After a fix is applied, ALWAYS push the changes using push_to_github(). Then confirm to the user briefly: 'Fixed and deployed.'",
        "If you detect an error yourself (not via SYSTEM_ALERT), briefly tell the user 'Found an issue, fixing now.' then fix it.",
        # --- Scheduling & Heartbeat ---
        "Use add_scheduled_task() to schedule recurring work. Do NOT talk about the scheduling process or re-scheduling.",
        "add_scheduled_task() runs the task IMMEDIATELY on creation by default (run_immediately=True) \u2014 so the user sees the first result right away without waiting for the cron tick. Only set run_immediately=False for tasks that must not run at creation time.",
        "CRITICAL: Job type decision rule \u2014 use 'inline_script' for ANY task that can be expressed as deterministic code: sending a message, picking from a list, calling an API, reading a file, computing a value, sending an emoji, etc. ONLY use 'subagent_recall' when the task genuinely requires LLM reasoning, creativity, or open-ended decision-making. Using subagent_recall for a simple 'pick random X and send it' is WRONG \u2014 use inline_script instead.",
        "inline_script: script code goes in subagent_instructions (Python by default; add '#lang: sh' or '#lang: js' on line 1 for shell/Node). TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are available as environment variables inside the script. Use verbose=False to suppress output notifications.",
        "The heartbeat system monitors teams. Handle alerts by fixing and pushing.",
        # --- Chat Control ---
        "Every user message starts with [MSG_META chat_id=... message_id=...]. These are Telegram IDs you can use with reply_to_message, pin_message, etc.",
        "When the user replies to a previous message, you will receive [REPLY CONTEXT] with the replied-to message_id, author, content, media type, and any quoted text the user highlighted.",
        "Use reply_to_message(chat_id, message_id, text) to directly reply to a specific message in the chat thread. This is useful when referencing earlier messages.",
        "Use pin_message(chat_id, message_id) to pin a message. Use unpin_message to unpin. These are powerful chat-management tools — use them when the user asks.",
        "If the user replies to a message or quotes a section and asks a question about it, focus your answer on that specific referenced content.",
        # --- Truthfulness & Design ---
        "Never invent tool outputs. If a tool returns an error, fix it or report it briefly if unfixable.",
    ]

    agent = Agent(
        model=model,
        db=db,
        description=f"Nova: Project Manager & Central Coordinator (chat_id={chat_id})",
        instructions=instructions,
        tools=agent_tools,
        markdown=False,
        add_history_to_context=True,
        add_datetime_to_context=True,
        learning=True,
        num_history_runs=6,
    )

    return agent
