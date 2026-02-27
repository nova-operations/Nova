"""
Subagent Action Tracker Tool

This tool allows subagents to report their current action status
for the heartbeat monitoring system.
"""

from typing import Optional
from nova.tools.subagent import (
    SUBAGENTS,
    update_subagent_action,
    get_current_subagent_context,
)
import logging

logger = logging.getLogger(__name__)


def report_subagent_action(
    action: str,
    subagent_id: Optional[str] = None,
) -> str:
    """
    Report the current action a subagent is performing.
    This updates the heartbeat status to show what the subagent is actively doing.

    The subagent_id can be provided explicitly, or it will be auto-detected
    from the current context.

    Args:
        action: A brief description of what the subagent is currently doing
                (e.g., "Searching Reuters for nuclear updates...", "Writing code for X...")
        subagent_id: The ID of the subagent (optional - auto-detected if not provided)

    Returns:
        Confirmation message
    """
    # Auto-detect subagent_id if not provided
    if subagent_id is None:
        subagent_id = get_current_subagent_context()

    if subagent_id is None:
        return "Error: Could not determine subagent ID. Please provide it explicitly."

    if subagent_id not in SUBAGENTS:
        return f"Error: Subagent {subagent_id} not found."

    update_subagent_action(subagent_id, action)
    logger.info(f"Subagent {subagent_id} reported action: {action}")
    return f"✅ Action updated: {action}"
