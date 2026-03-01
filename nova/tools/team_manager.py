"""
Team Manager — Multi-Project Parallel Team Orchestration

Nova uses this to spawn specialist teams using agno's native Team class.
Multiple teams can run concurrently for different projects or parallel tasks.

Memory Architecture:
- All agents share ONE db (nova_shared_agent_sessions) — same user_id = shared memories
- update_memory_on_run=True on all agents ensures cross-agent memory writes
- The chat_id IS the user_id — so Nova + all specialists share the same memory pool per user
"""

import os
import asyncio
import logging
from typing import List, Optional
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from agno.tools.tavily import TavilyTools
from nova.db.shared_memory import get_shared_db
from nova.tools.specialist_registry import get_specialist_config, list_specialists
from nova.tools.registry import get_tools_by_names
from nova.tools.subagent import SUBAGENTS
from nova.tools.streaming_utils import send_live_update, strip_all_formatting

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Singleton model factory
# ─────────────────────────────────────────────


def _get_model(model_id: str = None) -> OpenAIChat:
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "google/gemini-2.5-flash-preview")
    return OpenAIChat(
        id=model_id,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )


def _get_subagent_model(model_id: str = None) -> OpenAIChat:
    if model_id is None:
        model_id = os.getenv("SUBAGENT_MODEL", "minimax/minimax-m2.5")
    return OpenAIChat(
        id=model_id,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )


# ─────────────────────────────────────────────
# Specialist instantiation — shared memory
# ─────────────────────────────────────────────


def _create_specialist(name: str) -> Optional[Agent]:
    """
    Instantiate a specialist Agent from DB config.

    All specialists use get_shared_db() — the same session table as Nova.
    When combined with the same user_id at .arun() time, memories created
    by one specialist automatically become available to all others and to Nova.
    """
    config = get_specialist_config(name)
    if not config:
        logger.error(f"Specialist '{name}' not found. Available: {list_specialists()}")
        return None

    tools = get_tools_by_names(config.get("tools", []))

    # Always add Tavily as the first tool (if key available)
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        tools = [TavilyTools(api_key=tavily_key)] + tools

    # KEY: use shared DB so all agents share the same memory pool
    db = get_shared_db()

    instructions = (
        f"You are {config['role']}. Be concise and accurate. "
        f"Only report what you verified. "
        f"Never hallucinate tool outputs.\n\n" + config.get("instructions", "")
    )

    return Agent(
        name=config["name"],
        role=config["role"],
        model=_get_subagent_model(config.get("model")),
        instructions=instructions,
        tools=tools,
        db=db,
        markdown=False,
        add_history_to_context=True,
        add_datetime_to_context=True,
        learning=True,
        update_memory_on_run=True,  # Write memories after each run — visible to all agents
        num_history_runs=4,
    )


# ─────────────────────────────────────────────
# Core: run_team — Nova's primary delegation tool
# ─────────────────────────────────────────────


async def run_team(
    task_name: str,
    specialist_names: List[str],
    task_description: str,
    chat_id: Optional[str] = None,
    project: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """
    Spawns a specialist team and runs a task asynchronously.
    Multiple calls run concurrently — enabling true multi-project parallelism.

    Memory sharing:
    - All specialists share get_shared_db() — same session table as Nova
    - user_id = chat_id ensures memories are keyed to the same user
    - update_memory_on_run=True means every run updates the shared memory pool
    - Agents in different calls WILL see memories from previous calls by any agent

    Args:
        task_name: Short descriptive name for this task (e.g. "Fix Login Bug")
        specialist_names: List of specialist names (e.g. ["Bug-Fixer", "Tester"])
        task_description: Full task description with context
        chat_id: Telegram chat ID for live updates (also used as user_id)
        project: Optional project name for namespacing
        user_id: Optional explicit user_id override (defaults to chat_id)

    Returns:
        Team ID string (task runs in background)
    """
    try:
        # Build specialists — all share the same DB
        members = []
        missing = []
        for name in specialist_names:
            agent = _create_specialist(name)
            if agent:
                members.append(agent)
            else:
                missing.append(name)

        if not members:
            return f"Error: Could not instantiate any specialists. Missing: {missing}"

        if missing:
            logger.warning(f"Team '{task_name}' missing specialists: {missing}")

        # Namespace by project if provided
        team_label = f"[{project}] {task_name}" if project else task_name
        team_id = f"team_{task_name}_{asyncio.get_event_loop().time():.0f}"

        # KEY: user_id is the chat_id — ALL agents (Nova + specialists) use the same
        # user_id so their memories are merged in the shared DB.
        run_user_id = user_id or chat_id or "nova_system"

        # Build the agno Team — also on the shared DB
        team = Team(
            name=team_label,
            members=members,
            model=_get_model(),
            db=get_shared_db(),
            description=f"Specialist team for: {team_label}",
            instructions=[
                "Coordinate to complete the task. Be concise and accurate.",
                "Only report verified results. Never hallucinate.",
                "Delegate subtasks to the most appropriate team member.",
                "After fixing code, use push_to_github() to commit and push changes.",
                "You have shared memory with Nova and all other specialists — use it.",
            ],
            markdown=False,
            add_datetime_to_context=True,
            learning=True,
            update_memory_on_run=True,  # Team-level shared memory writes
        )

        # Register in global SUBAGENTS dict for heartbeat tracking
        SUBAGENTS[team_id] = {
            "name": team_label,
            "status": "starting",
            "result": None,
            "chat_id": chat_id,
            "project": project,
        }

        async def _run():
            """Background runner with live updates and error recovery."""
            try:
                SUBAGENTS[team_id]["status"] = "running"

                # Pass the same user_id to the team — this is what merges memory pools
                response = await team.arun(
                    task_description,
                    user_id=run_user_id,
                )
                result = response.content if response else "No result."

                SUBAGENTS[team_id]["status"] = "completed"
                SUBAGENTS[team_id]["result"] = result

                # Auto-push if the team made code changes
                try:
                    from nova.tools.github_tools import push_to_github, get_git_status

                    git_status = get_git_status()
                    if (
                        "M " in git_status
                        or "A " in git_status
                        or "ahead" in git_status.lower()
                    ):
                        push_result = push_to_github(
                            commit_message=f"fix: {task_name} - auto-push after team completion",
                            skip_tests=False,
                        )
                        logger.info(f"Auto-push after team '{team_label}': {push_result}")
                except Exception as push_err:
                    logger.warning(f"Auto-push failed for team '{team_label}': {push_err}")

                if chat_id:
                    await send_live_update(
                        strip_all_formatting(result)[:2000],
                        chat_id,
                    )

            except Exception as e:
                SUBAGENTS[team_id]["status"] = "failed"
                SUBAGENTS[team_id]["result"] = str(e)
                logger.error(f"Team '{team_label}' failed: {e}")

                if chat_id:
                    from nova.telegram_bot import reinvigorate_nova

                    asyncio.create_task(
                        reinvigorate_nova(
                            chat_id,
                            f"SYSTEM_ALERT: Team '{team_label}' (ID: {team_id}) failed.\n"
                            f"Error: {str(e)}\n"
                            f"Task was: {task_description[:500]}\n"
                            f"Decide: spawn a Bug-Fixer team, or run parallel fix+alternative approach.",
                        )
                    )

        asyncio.create_task(_run())
        return f"Team '{team_label}' launched. ID: {team_id}. Specialists: {[m.name for m in members]}"

    except Exception as e:
        logger.error(f"run_team error: {e}")
        return f"Error launching team: {e}"
