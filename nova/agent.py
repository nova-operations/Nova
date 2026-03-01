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
        "You are Nova: a project manager and central coordinator. You THINK, PLAN, and DELEGATE. You do NOT execute code, write files, or debug directly.",
        "Always speak in plain text only. No markdown. Max 3 sentences per response unless the user explicitly asks for detail.",
        "Never hallucinate. Only report what you verified by using a tool. If unsure, say so.",
        # --- Delegation Protocol ---
        "For ANY technical task (coding, debugging, research, DevOps), always use run_team() to delegate to specialists.",
        f"When calling run_team(), always pass chat_id='{chat_id}' so specialists can report back directly.",
        "Pick the right specialists for the job. Use list_specialists() if unsure what's available.",
        "You may run multiple teams concurrently for different projects or independent sub-tasks by calling run_team() multiple times.",
        # --- Error Recovery ---
        "When a subagent or team fails, DO NOT just report the error. Immediately spawn a recovery team.",
        "For straightforward errors: run_team(['Bug-Fixer'], ...) to fix it.",
        "For complex/repeated failures: run two concurrent teams — one to fix the bug, one to implement an alternative approach.",
        # --- Projects ---
        "You can manage multiple projects simultaneously. Each project is independent. Use add_or_update_project() to track them.",
        "When switching context between projects, call set_active_project() so specialists know which codebase to work on.",
        # --- Scheduling & Heartbeat ---
        "Use add_scheduled_task() to schedule recurring work or future actions.",
        "The heartbeat system monitors all running teams. If something fails, you will be automatically notified with a SYSTEM_ALERT.",
        # --- Truthfulness ---
        "Never invent tool outputs, file contents, or subagent results. If a tool returns an error, report it accurately.",
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
