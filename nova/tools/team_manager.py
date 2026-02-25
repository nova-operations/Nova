import os
import asyncio
import logging
from typing import List, Optional, Dict
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from nova.db.engine import get_agno_db
from nova.tools.specialist_registry import get_specialist_config
from nova.tools.registry import get_tools_by_names
from nova.tools.subagent import SUBAGENTS
from nova.tools.streaming_utils import (
    send_streaming_start,
    send_streaming_progress,
    send_streaming_complete,
    send_streaming_error,
    StreamingContext
)

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
    db = get_agno_db(session_table=f"specialist_{name}_sessions")

    # MANDATORY SAU INSTRUCTIONS for specialists
    sau_instructions = """## MANDATORY LIVE UPDATES (SAU) - DEFAULT BEHAVIOR:
- YOU MUST use the streaming system to report milestones IMMEDIATELY as you progress.
- Use the `send_streaming_start`, `send_streaming_progress`, and `send_streaming_complete` functions.
- The header format for all updates is: [SAU: {agent_name}]
- Report at key milestones: initialization, tool execution, results processing, completion.
- NEVER wait for completion to send updates - report progress in real-time.
- If errors occur, use `send_streaming_error` immediately.
- This is NOT optional - it is the MANDATORY default for all subagent/specialist reporting.
- Legacy heartbeat/PM polling is DISABLED for your tasks.
"""

    # Inject SAU instructions into the specialist's existing instructions
    enhanced_instructions = sau_instructions + "\n\n" + config.get("instructions", "")

    return Agent(
        name=config["name"],
        role=config["role"],
        model=model,
        instructions=enhanced_instructions,
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
    Uses SAU (Subagent Automatic Updates) as the mandatory reporting mechanism.
    Heartbeat system is DISABLED for team tasks.
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

        # Team setup with mandatory SAU instructions
        team_instructions = """## MANDATORY LIVE UPDATES (SAU):
This team MUST use SAU streaming updates for all progress reporting.
The header format is: [SAU: {team_name}]
- Report when team initializes
- Report when individual specialists start working
- Report major milestones and completion
- DO NOT rely on heartbeat polling - SAU is the primary reporting channel.
"""
        
        team = Team(
            name=task_name,
            members=members,
            description=f"Dynamic Team for: {task_name}",
            instructions=team_instructions,
            markdown=True,
        )

        subagent_id = f"team_{task_name}_{asyncio.get_event_loop().time():.0f}"

        # Heartbeat monitoring DISABLED - SAU is mandatory
        logger.info(f"Team task '{task_name}' created - SAU live updates enabled (heartbeat disabled)")

        # Store in global tracking
        SUBAGENTS[subagent_id] = {
            "name": task_name,
            "status": "running",
            "result": None,
            "chat_id": chat_id,
        }

        # Run in background via task with SAU updates
        async def _team_runner():
            # Create SAU streaming context for the team
            async with StreamingContext(chat_id, f"Team: {task_name}", auto_complete=False) as stream:
                try:
                    await stream.send(f"Initializing {len(members)} specialists...")
                    
                    response = await team.arun(task_description)
                    
                    SUBAGENTS[subagent_id]["status"] = "completed"
                    SUBAGENTS[subagent_id]["result"] = response.content
                    
                    await stream.send("Team task completed successfully!")
                    
                except Exception as e:
                    error_msg = str(e)
                    SUBAGENTS[subagent_id]["status"] = "failed"
                    SUBAGENTS[subagent_id]["result"] = f"Error: {error_msg}"
                    
                    await stream.send(f"Team task failed: {error_msg}", msg_type="error")

        if chat_id:
            from nova.telegram_bot import notify_user

            asyncio.create_task(
                notify_user(
                    chat_id,
                    f"👥 <b>Starting Team Task:</b> {task_name} ({len(members)} specialists)",
                )
            )
            
            # Send SAU start notification
            asyncio.create_task(
                send_streaming_start(chat_id, f"Team: {task_name}")
            )

        asyncio.create_task(_team_runner())

        return f"🚀 Team task '{task_name}' started with {len(members)} specialists. ID: {subagent_id}"

    except Exception as e:
        return f"❌ Error launching team task: {e}"