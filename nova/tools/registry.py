from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import (
    read_file,
    write_file,
    list_files,
    delete_file,
    create_directory,
)
from nova.tools.github_tools import push_to_github, pull_latest_changes
from nova.tools.scheduler import (
    add_scheduled_task,
    list_scheduled_tasks,
    get_scheduled_task,
)
from nova.tools.mcp_tools import add_mcp_server, list_registered_mcp_servers

# Mapping for dynamic agent tool assignment
from nova.tools.web_search import web_search

TOOL_REGISTRY = {
    "shell": execute_shell_command,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "delete_file": delete_file,
    "create_directory": create_directory,
    "github_push": push_to_github,
    "github_pull": pull_latest_changes,
    "scheduler_add": add_scheduled_task,
    "scheduler_list": list_scheduled_tasks,
    "mcp_add": add_mcp_server,
    "mcp_list": list_registered_mcp_servers,
    "web_search": web_search,
}


def get_tools_by_names(names: list):
    """Returns a list of tool functions based on names."""
    if not names:
        return []
    return [TOOL_REGISTRY[name] for name in names if name in TOOL_REGISTRY]
