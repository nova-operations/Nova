import os
import asyncio
import logging
from typing import List, Optional, Dict
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from agno.db.postgres import PostgresDb
from agno.db.sqlite import SqliteDb
from nova.tools.specialist_registry import get_specialist_config
from nova.tools.registry import get_tools_by_names
from nova.tools.heartbeat import register_subagent_for_heartbeat
from nova.tools.subagent import SUBAGENTS

logger = logging.getLogger(__name__)


def create_specialist_agent(
    name: str, session_id: Optional[str] = None
) -> Optional[Agent]:
    """Instantiate a specialist agent from DB configuration."""
    config = get_specialist_config(name)
    if not config:
        logger.error(f"Specialist '{name}' not found in registry.")
        return None

    api_key = os.getenv("OPENROUTER_API_KEY")
    model = OpenAIChat(
        id=config["model"],
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    # Tool assignment
    tools = get_tools_by_names(config["tools"])

    # DB setup for persistent specialist memory
    database_url = os.getenv("DATABASE_URL")
    db = None
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        db = PostgresDb(
            session_table=f"specialist_{name}_sessions", db_url=database_url
        )

    return Agent(
        name=config["name"],
        role=config["role"],
        model=model,
        instructions=config["instructions"],
        tools=tools,
        db=db,
        markdown=True,
        add_history_to_context=True,
    )


async def run_team_task(
    task_name: str,
    specialist_names: List[str],
    task_description: str,
    chat_id: Optional[str] = None,
) -> str:
    """
    Creates a dynamic team and runs a task asynchronously.
    Integrates with the heartbeat system.
    """
    try:
        # Build specialists
        members = []
        for s_name in specialist_names:
            agent = create_specialist_agent(s_name)
            if agent:
                members.append(agent)

        if not members:
            return "❌ Error: Could not instantiate any specialists for the team."

        # Team setup
        # The first specialist is usually the primary, but Team leader manages them
        # We'll use a generic Team model for coordination
        team = Team(
            name=task_name,
            members=members,
            description=f"Dynamic Team for: {task_name}",
            instructions="Collaborate to solve the task. Delegate sub-tasks if needed.",
            markdown=True,
        )

        subagent_id = f"team_{task_name}_{asyncio.get_event_loop().time():.0f}"

        # Register for heartbeat
        register_subagent_for_heartbeat(
            subagent_id, f"Team: {task_name}", chat_id=chat_id
        )

        # Store in global tracking
        SUBAGENTS[subagent_id] = {
            "name": task_name,
            "status": "running",
            "result": None,
            "chat_id": chat_id,
        }

        # Run in background via task
        async def _team_runner():
            try:
                response = await team.arun(task_description)
                SUBAGENTS[subagent_id]["status"] = "completed"
                SUBAGENTS[subagent_id]["result"] = response.content
            except Exception as e:
                SUBAGENTS[subagent_id]["status"] = "failed"
                SUBAGENTS[subagent_id]["result"] = f"Error: {str(e)}"

        if chat_id:
            from nova.telegram_bot import notify_user

            asyncio.create_task(
                notify_user(
                    chat_id,
                    f"👥 <b>Starting Team Task:</b> {task_name} ({len(members)} specialists)",
                )
            )

        asyncio.create_task(_team_runner())

        return f"🚀 Team task '{task_name}' started with {len(members)} specialists. ID: {subagent_id}"

    except Exception as e:
        return f"❌ Error launching team task: {e}"
