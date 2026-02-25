import os
from typing import Optional
from sqlalchemy import create_engine
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from agno.skills import Skills, LocalSkills

# Tool imports
from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import (
    read_file,
    write_file,
    list_files,
    delete_file,
    create_directory,
)
from nova.tools.subagent import (
    create_subagent,
    list_subagents,
    get_subagent_result,
    kill_subagent,
)
from nova.tools.github_tools import push_to_github, pull_latest_changes
from nova.tools.scheduler import (
    add_scheduled_task,
    list_scheduled_tasks,
    get_scheduled_task,
    update_scheduled_task,
    remove_scheduled_task,
    pause_scheduled_task,
    resume_scheduled_task,
    run_scheduled_task_now,
    get_scheduler_status,
    start_scheduler,
    stop_scheduler,
)
from nova.tools.heartbeat import (
    start_heartbeat_monitor,
    stop_heartbeat_monitor,
    register_subagent_for_heartbeat,
    unregister_subagent_from_heartbeat,
    get_heartbeat_status,
    get_heartbeat_detailed_status,
    auto_register_active_subagents,
)
from nova.tools.mcp_registry import mcp_registry
from nova.tools.mcp_tools import (
    add_mcp_server,
    remove_mcp_server,
    list_registered_mcp_servers,
)
from nova.tools.specialist_registry import save_specialist_config, list_specialists
from nova.tools.team_manager import run_team_task
from nova.logger import setup_logging

try:
    from agno.tools.mcp import MCPTools, StreamableHTTPClientParams

    try:
        from agno.tools.mcp import StdioServerParameters
    except ImportError:
        try:
            from agno.tools.mcp.mcp import StdioServerParameters
        except ImportError:
            StdioServerParameters = None
except ImportError:
    from agno.tools.mcp import MCPTools

    StreamableHTTPClientParams = None
    StdioServerParameters = None

load_dotenv()
setup_logging()

# Global Tool Cache to avoid redundant MCP connections
_CACHED_TOOLS = None


def get_mcp_toolkits():
    """Builds and returns the list of MCP toolkits (cached)."""
    global _CACHED_TOOLS
    if _CACHED_TOOLS is not None:
        return _CACHED_TOOLS

    toolkits = []

    # 1. Standard Agno Docs
    try:
        toolkits.append(
            MCPTools(
                "agno_docs",
                server_params=StreamableHTTPClientParams(
                    url="https://docs.agno.com/mcp", timeout=120
                ),
            )
        )
    except Exception as e:
        print(f"⚠️ Warning: Failed to load Agno Docs MCP: {e}")

    # 2. Custom MCPs from Registry
    try:
        registered_servers = mcp_registry.list_servers()
        for s in registered_servers:
            try:
                name = s.get("name", "unknown")
                if name == "agno_docs":
                    continue  # skip redundant

                print(f"🔌 Initializing MCP Server: {name}...")
                if s["transport"] == "stdio":
                    params = StdioServerParameters(
                        command=s["command"],
                        args=s["args"],
                        env=s["env"] or os.environ.copy(),
                    )
                    toolkits.append(MCPTools(name, server_params=params))
                else:
                    params = StreamableHTTPClientParams(
                        url=s["url"], headers=s.get("env"), timeout=120
                    )
                    toolkits.append(MCPTools(name, server_params=params))
            except Exception as e:
                print(f"❌ Error setting up MCP server {s.get('name')}: {e}")
    except Exception as e:
        print(f"⚠️ Warning: Registry error: {e}")

    _CACHED_TOOLS = toolkits
    return toolkits


def get_agent(model_id: Optional[str] = None, chat_id: Optional[str] = None):
    """
    Creates and returns a configured Agno Agent (Nova).
    Nova acts as a Project Manager that spawns subagents and provides heartbeats.
    """
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "google/gemini-2.0-flash-001")
    api_key = os.getenv("OPENROUTER_API_KEY")

    model = OpenAIChat(
        id=model_id,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    chat_id = chat_id or "unknown"

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
            db = PostgresDb(session_table="nova_agent_sessions", db_url=database_url)
        elif "sqlite" in database_url:
            # SQLite
            # Use create_engine to validate the URL, but still use SqliteDb for Agno
            try:
                create_engine(database_url)  # Validate URL
                db = SqliteDb(
                    db_file=database_url.replace("sqlite:///", "")
                )  # Extract path for SqliteDb
            except Exception as e:
                print(
                    f"Warning: Invalid SQLite DATABASE_URL '{database_url}'. Falling back to default SQLite path. Error: {e}"
                )
                db_path = os.getenv("SQLITE_DB_PATH", "/app/data/nova_memory.db")
                os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
                db = SqliteDb(db_file=db_path)
        else:
            # Fallback for other potential types, try to use it as a generic URL for PostgresDb
            # This might fail if it's not a valid Postgres URL, but allows flexibility
            try:
                create_engine(database_url)  # Validate URL
                db = PostgresDb(
                    session_table="nova_agent_sessions", db_url=database_url
                )
            except Exception as e:
                print(
                    f"Warning: DATABASE_URL '{database_url}' is not a recognized type or invalid. Falling back to default SQLite path. Error: {e}"
                )
                db_path = os.getenv("SQLITE_DB_PATH", "/app/data/nova_memory.db")
                os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
                db = SqliteDb(db_file=db_path)
    else:
        # Resolve path locally or in container
        db_path = os.getenv("SQLITE_DB_PATH", "data/nova_memory.db")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        except OSError:
            # Fallback to current directory if specified path is not writable
            db_path = "nova_memory.db"
        db = SqliteDb(db_file=db_path)

    # Skills paths with local fallbacks
    repo_skills_path = os.getenv("REPO_SKILLS_PATH", "data/nova_repo/skills")
    persistent_skills_path = os.getenv("PERSISTENT_SKILLS_PATH", "data/skills")

    for p in [repo_skills_path, persistent_skills_path]:
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            # Fallback to local 'skills' dir if /app is requested but not writable
            local_p = os.path.join(os.getcwd(), "skills")
            os.makedirs(local_p, exist_ok=True)

    # Add all tools (PM logic + MCP)
    agent_tools = [
        execute_shell_command,
        read_file,
        write_file,
        list_files,
        delete_file,
        create_directory,
        create_subagent,
        list_subagents,
        get_subagent_result,
        kill_subagent,
        push_to_github,
        pull_latest_changes,
        start_heartbeat_monitor,
        stop_heartbeat_monitor,
        register_subagent_for_heartbeat,
        unregister_subagent_from_heartbeat,
        get_heartbeat_status,
        get_heartbeat_detailed_status,
        auto_register_active_subagents,
        add_mcp_server,
        remove_mcp_server,
        list_registered_mcp_servers,
        add_scheduled_task,
        list_scheduled_tasks,
        get_scheduled_task,
        update_scheduled_task,
        remove_scheduled_task,
        pause_scheduled_task,
        resume_scheduled_task,
        run_scheduled_task_now,
        get_scheduler_status,
        start_scheduler,
        stop_scheduler,
        save_specialist_config,
        list_specialists,
        run_team_task,
    ]

    # Append the cached MCP toolkits
    agent_tools.extend(get_mcp_toolkits())

    agent = Agent(
        model=model,
        db=db,
        description="I am Nova, the Project Manager AI. I solve complex tasks by coordinating teams of subagents.",
        instructions=[
            "## ROLE: PROJECT MANAGER (PM)",
            "You are Nova. Your primary responsibility is to orchestrate solutions using specialized subagents.",
            "## OPERATIONAL WORKFLOW:",
            "1. **Analyze & Delegate**: For every user request, analyze the requirements and SPAWN one or more subagents using `create_subagent`.",
            f"   - IMPORTANT: Always pass `chat_id='{chat_id}'` to `create_subagent` so I can send updates.",
            "2. **Heartbeat Protocol**: While subagents are working, the system will automatically send updates every 30 seconds to the user.",
            "   - Use `get_heartbeat_status` to get a status report",
            "   - Use `start_heartbeat_monitor` to enable background monitoring",
            "   - New subagents are automatically registered with the heartbeat system",
            "3. **Monitor Progress**: Use `list_subagents`, `get_subagent_result`, and `get_heartbeat_status` to track the state of your team.",
            "4. **Synthesis**: Once subagents complete their tasks, gather their outputs and provide a final synthesized response to the user.",
            "## CRITICAL RULE: DELEGATION ONLY",
            "You are a HIGH-LEVEL STRATEGIST. Do NOT perform research, file modifications, or shell commands yourself.",
            "For every user request, your workflow MUST be:",
            "1. Analyze the request and DESIGN a specialist agent.",
            "2. SPAWN the subagent using `create_subagent`.",
            "3. WAIT for completion (monitor via heartbeats).",
            "4. COLLECT results with `get_subagent_result` and provide SYNTHESIS.",
            "Violating this rule by doing work yourself is a failure of your instructions."
            "## HEARTBEAT SYSTEM:",
            "The heartbeat system automatically monitors subagents in the background:",
            "- `start_heartbeat_monitor(30)`: Start background monitoring (check every 30 seconds)",
            "- `get_heartbeat_status()`: Get a formatted status report of all active subagents",
            "- `get_heartbeat_detailed_status()`: Get detailed JSON status",
            "- Subagents are automatically registered when created",
            "- The system warns if a subagent runs for >2 minutes without completing",
            "## TOOLS & SKILLS:",
            "- You have full access to the filesystem and shell.",
            "- You use PostgreSQL for persistent memory of MCP configurations and agent states.",
            "- You use Agno MCP tools to fetch the latest documentation and remain 'state-of-the-art'.",
            "- You have access to a scheduler system for automated tasks.",
            "## SCHEDULER TOOLS:",
            "- `add_scheduled_task`: Schedule new tasks (cron format)",
            "- `list_scheduled_tasks`: List all scheduled tasks",
            "- `get_scheduled_task`: Get details of a specific task",
            "- `update_scheduled_task`: Modify an existing task",
            "- `remove_scheduled_task`: Delete a scheduled task",
            "- `pause_scheduled_task`: Pause a task",
            "- `resume_scheduled_task`: Resume a paused task",
            "- `run_scheduled_task_now`: Trigger a task manually",
            "- `get_scheduler_status`: Check scheduler health",
            "## DYNAMIC TEAM ORCHESTRATION:",
            "- You have a PRODUCTION-READY registry for specialists. Use it to build reusable expertise.",
            "- `save_specialist_config`: Register a new specialist (e.g. 'SecurityAudit', 'FrontendDev'). This survives reboots.",
            "- `list_specialists`: See what experts you already have in your roster.",
            "- `run_team_task`: The HIGHEST form of delegation. Spawn a collaborative team of specialists to solve a task.",
            f"   - ALWAYS pass `chat_id='{chat_id}'` so the team can report results.",
            f"   - E.g. `run_team_task(task_name='WebsiteBuild', specialist_names=['Coder', 'Researcher'], task_description='Build a site', chat_id='{chat_id}')`",
            "## COLLABORATION:",
            "- Always treat subagents as your team members. Provide them with clear, detailed instructions.",
            "- Use the Specialist Registry for complex, recurring roles. Use `create_subagent` for simple, one-off tasks.",
        ],
        skills=Skills(
            loaders=[LocalSkills(repo_skills_path), LocalSkills(persistent_skills_path)]
        ),
        tools=agent_tools,
        markdown=True,
        add_history_to_context=True,
        update_memory_on_run=True,
        cache_session=True,
    )

    return agent


if __name__ == "__main__":
    # Initialize scheduler on startup
    from nova.tools.scheduler import initialize_scheduler

    initialize_scheduler()

    agent = get_agent()
    print("Nova PM Agent initialized.")
    print("Scheduler started - running in background.")
