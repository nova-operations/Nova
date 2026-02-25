"""
Heartbeat integration for subagents.

This module provides automatic heartbeat registration when subagents are created.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# We'll import heartbeat functions lazily to avoid circular imports
_heartbeat_module: Optional[object] = None

def _get_heartbeat():
    """Lazily import heartbeat module to avoid circular import."""
    global _heartbeat_module
    if _heartbeat_module is None:
        try:
            from nova.tools import heartbeat
            _heartbeat_module = heartbeat
        except ImportError as e:
            logger.warning(f"Could not import heartbeat module: {e}")
            return None
    return _heartbeat_module


def auto_register_with_heartbeat(subagent_id: str, name: str) -> str:
    """
    Automatically register a newly created subagent with the heartbeat monitor.
    
    This function is called from create_subagent to ensure all subagents
    are tracked by the heartbeat system.
    
    Args:
        subagent_id: The ID of the created subagent
        name: The name of the subagent
        
    Returns:
        Confirmation or error message
    """
    heartbeat = _get_heartbeat()
    if heartbeat is None:
        return "Heartbeat module not available"
    
    try:
        return heartbeat.register_subagent_for_heartbeat(subagent_id, name)
    except Exception as e:
        logger.error(f"Error registering subagent with heartbeat: {e}")
        return f"Subagent created but heartbeat registration failed: {e}"


def check_heartbeat_and_report() -> str:
    """
    Get a heartbeat status report for active subagents.
    This can be called periodically to provide updates.
    
    Returns:
        Formatted heartbeat status report
    """
    heartbeat = _get_heartbeat()
    if heartbeat is None:
        return "Heartbeat module not available"
    
    try:
        return heartbeat.get_heartbeat_status()
    except Exception as e:
        logger.error(f"Error getting heartbeat status: {e}")
        return f"Error getting heartbeat status: {e}"