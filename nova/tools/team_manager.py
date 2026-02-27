import os
import asyncio
import logging
from typing import List, Optional, Dict
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from nova.db.engine import get_agno_db
from nova.tools.specialist_registry import get_specialist_config, list_specialists
from nova.tools.registry import get_tools_by_names
from nova.tools.subagent import SUBAGENTS
from nova.tools.streaming_utils import (
    send_streaming_start,
    send_streaming_progress,
    send_streaming_complete,
    send_streaming_error,
    StreamingContext,
    strip_all_formatting,
)

logger = logging.getLogger(__name__)


def create_specialist_agent(
    name: str, session_id: Optional[str] = None
) -> Optional[Agent]:
    """Instantiate a specialist agent from DB configuration."""
    config = get_specialist_config(name)
    if not config:
        logger.error(f"Specialist '{name}' not found in registry.")
        logger.error(f"Available specialists: {list_specialists()}")
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
    sau_instructions = """## MANDATORY LIVE UPDATES (SAU) - REAL-TIME MODE:
- YOU MUST use the streaming system to report milestones IMMEDIATELY as you progress.
- Use the `send_streaming_start`, `send_streaming_progress`, and `send_streaming_complete` functions.
- The header format for all updates is: [SAU: {agent_name}]
- Report ONLY key milestones: initialization, tool execution, major results, completion.
- DO NOT stream every line of output or every internal thought.
- This is NOT optional - it is the MANDATORY default for all reporting.
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
        markdown=False,
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

    REAL-TIME MODE: Each step is sent as an individual message immediately.
    """
    try:
        # Build specialists
        members = []
        missing_specialists = []

        for s_name in specialist_names:
            agent = create_specialist_agent(s_name)
            if agent:
                members.append(agent)
            else:
                missing_specialists.append(s_name)
                logger.error(f"Failed to create specialist agent: {s_name}")

        if not members:
            available = list_specialists()
            error_msg = f"Error: Could not instantiate any specialists for the team."
            if missing_specialists:
                error_msg += f" Missing: {missing_specialists}. Available: {available}"
            logger.error(error_msg)
            return error_msg

        # If some specialists are missing, log warning but continue
        if missing_specialists:
            logger.warning(
                f"Team '{task_name}' running with reduced members. "
                f"Missing: {missing_specialists}"
            )

        # Team setup with mandatory SAU instructions
        team_instructions = """## MANDATORY LIVE UPDATES (SAU) - REAL-TIME MODE:
This team MUST use SAU streaming updates for all progress reporting.
The header format is: [SAU: {team_name}]
- Report key milestones as they occur.
- DO NOT stream every line of output or every internal thought.
- You can commit code, but you cannot push to remote. Nova PM handles all pushes.
"""

        team = Team(
            name=task_name,
            members=members,
            description=f"Dynamic Team for: {task_name}",
            instructions=team_instructions,
            markdown=False,
        )

        subagent_id = f"team_{task_name}_{asyncio.get_event_loop().time():.0f}"

        # Heartbeat monitoring DISABLED - SAU is mandatory
        logger.info(
            f"Team task '{task_name}' created - SAU live updates enabled (heartbeat disabled)"
        )

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
            async with StreamingContext(
                chat_id, f"Team: {task_name}", auto_complete=False
            ) as stream:
                try:
                    await stream.send(f"Initializing {len(members)} specialists...")

                    # Stream each step as it happens
                    for i, member in enumerate(members):
                        await stream.send(
                            f"Starting specialist {i+1}/{len(members)}: {member.name}..."
                        )
                        # Each specialist will send its own SAU updates

                    response = await team.arun(task_description)

                    SUBAGENTS[subagent_id]["status"] = "completed"
                    SUBAGENTS[subagent_id]["result"] = response.content

                    await stream.send("Team task completed successfully!")

                except Exception as e:
                    SUBAGENTS[subagent_id]["status"] = "failed"
                    SUBAGENTS[subagent_id]["result"] = str(e)
                    await stream.send(f"Team task failed: {str(e)}", msg_type="error")

                    # PROACTIVE RECOVERY: Wake up Nova
                    if chat_id:
                        from nova.telegram_bot import reinvigorate_nova

                        asyncio.create_task(
                            reinvigorate_nova(
                                chat_id, f"Team '{task_name}' failed: {str(e)}"
                            )
                        )

        if chat_id:
            # Send minimal notification that team is starting
            # The StreamingContext will handle the detailed updates
            from nova.telegram_bot import notify_user

            asyncio.create_task(
                notify_user(
                    chat_id,
                    f"Starting Team Task: {task_name} ({len(members)} specialists)",
                )
            )

            # Send SAU start notification
            asyncio.create_task(send_streaming_start(chat_id, f"Team: {task_name}"))

        asyncio.create_task(_team_runner())

        return f"Team task '{task_name}' started with {len(members)} specialists. ID: {subagent_id}"

    except Exception as e:
        return f"Error launching team task: {e}"
