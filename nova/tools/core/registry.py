"""
Tool Registry — maps string tool names to actual functions.
Used by the specialist registry to assign tools to agents.
Each specialist gets: up to 5 domain tools + TavilyTools (auto-added).
"""

from nova.tools.system.shell import execute_shell_command
from nova.tools.system.filesystem import (
    read_file,
    write_file,
    list_files,
    list_files_under_directory,
    delete_file,
    create_directory,
)
from nova.tools.github.github_tools import push_to_github, pull_latest_changes, get_git_status
from nova.tools.scheduler.scheduler import (
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
    pause_scheduled_task,
    resume_scheduled_task,
)
from nova.tools.web.web_search import web_search
from nova.tools.audio.audio_tool_wrapper import send_audio_message
from nova.tools.mcp.mcp_tools import (
    add_mcp_server,
    remove_mcp_server,
    list_registered_mcp_servers,
)
from nova.tools.core.system_state import get_system_state


# ─────────────────────────────────────────────
# All available specialist tools (max 5 per specialist)
# ─────────────────────────────────────────────

TOOL_REGISTRY = {
    # Filesystem
    "read_file": read_file,
    "read_file_content": read_file,  # alias for read_file
    "read": read_file,  # alias for read_file
    "open_file": read_file,  # alias for read_file
    "write_file": write_file,
    "write": write_file,  # alias for write_file
    "list_files": list_files,
    "ls": list_files,  # alias for list_files
    "list_files_under_directory": list_files_under_directory,
    "delete_file": delete_file,
    "delete": delete_file,  # alias for delete_file
    "create_directory": create_directory,
    "mkdir": create_directory,  # alias for create_directory
    # Shell - multiple aliases for shell execution
    "shell": execute_shell_command,
    "bash": execute_shell_command,
    "sh": execute_shell_command,
    "execute_shell_command": execute_shell_command,
    # Git
    "github_push": push_to_github,
    "github_pull": pull_latest_changes,
    "git_status": get_git_status,
    # Web Search (optimized)
    "web_search": web_search,
    "google_search": web_search,
    "search": web_search,
    "get_latest_news": web_search,
    "search_news": web_search,
    # Audio/TTS
    "send_audio_message": send_audio_message,
    "voice_summary": send_audio_message,
    "send_voice": send_audio_message,
    # MCP
    "add_mcp_server": add_mcp_server,
    "remove_mcp_server": remove_mcp_server,
    "list_mcp_servers": list_registered_mcp_servers,
    # System State
    "get_system_state": get_system_state,
    # Scheduling (for DevOps specialists)
    "add_scheduled_task": add_scheduled_task,
    "scheduler_add": add_scheduled_task,
    "scheduler_list": list_scheduled_tasks,
    "remove_scheduled_task": remove_scheduled_task,
    "scheduler_remove": remove_scheduled_task,
    "pause_scheduled_task": pause_scheduled_task,
    "resume_scheduled_task": resume_scheduled_task,
}


def get_tools_by_names(names: list) -> list:
    """
    Returns tool functions by name. Unknown names are skipped with a warning.
    Note: TavilyTools is added automatically by the specialist builder — do NOT include here.
    """
    import logging
    import os

    logger = logging.getLogger(__name__)
    github_token = os.getenv("GITHUB_TOKEN")

    tools = []
    for name in names:
        if name in ["github_push", "github_pull", "git_status"] and not github_token:
            logger.warning(f"Tool '{name}' skipped because GITHUB_TOKEN is not set.")
            continue
            
        if name in TOOL_REGISTRY:
            tools.append(TOOL_REGISTRY[name])
        elif name not in ("web_search", "tavily", "web_search_using_tavily"):
            # Silently ignore tavily references (handled separately), warn for unknown
            logger.warning(f"Tool '{name}' not found in registry, skipping.")
    return tools