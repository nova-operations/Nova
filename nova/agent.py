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

from nova.db.engine import get_agno_db
from nova.tools.shell import execute_shell_command
from nova.tools.team_manager import run_team
from nova.tools.system_state import get_system_state
from nova.tools.scheduler import (
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
)
from nova.tools.project_manager import (
    add_or_update_project,
    list_projects,
    set_active_project,
)
from nova.tools.specialist_registry import save_specialist_config, list_specialists
from nova.logger import setup_logging

load_dotenv()
setup_logging()


def get_model(model_id: str = None) -> OpenAIChat:
    """Returns a configured model via OpenRouter."""
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "google/gemini-2.5-flash-preview")
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
    db = get_agno_db(session_table="nova_agent_sessions")

    tavily_api_key = os.getenv("TAVILY_API_KEY")

    # Nova's 5 core tools + Tavily. That's it.
    agent_tools = [
        TavilyTools(api_key=tavily_api_key) if tavily_api_key else None,
        run_team,  # Primary delegation tool
        get_system_state,  # Check running agents/tasks
        add_scheduled_task,  # Schedule future work
        list_scheduled_tasks,  # See what's scheduled
        remove_scheduled_task,  # Remove scheduled tasks
        add_or_update_project,  # Register a project
        list_projects,  # See all projects
        set_active_project,  # Switch active project
        save_specialist_config,  # Define/update specialists
        list_specialists,  # See available specialists
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
        "CRITICAL: If a specialist or task is missing or failed, fix it SILENTLY (re-register/re-schedule). DO NOT tell the user you are repairing the system.",
        "CRITICAL: If responding to a [SYSTEM_ALERT], say NOTHING to the user. Fix it silently using specialists.",
        "Never hallucinate. Only report what you verified by using a tool.",
        # --- Delegation Protocol ---
        "For technical tasks, use run_team() to delegate. Always pass chat_id='{chat_id}'.",
        "Wait for verified results. Do NOT pre-announce or predict outcomes. Just act then wait.",
        # --- Error Recovery ---
        "When a task fails, immediately spawn a recovery team (e.g., ['Bug-Fixer']). Fix it SILENTLY.",
        "Do NOT report technical errors to the user if you can fix them. Just fix it and report the eventual success.",
        # --- Scheduling & Heartbeat ---
        "Use add_scheduled_task() to schedule recurring work. Do NOT talk about the scheduling process or re-scheduling.",
        "The heartbeat system monitors teams. Handle alerts SILENTLY.",
        # --- Truthfulness ---
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
        update_memory_on_run=True,
        num_history_runs=6,
    )

    return agent
