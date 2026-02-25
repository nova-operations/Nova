import os
import json
import asyncio
from typing import Optional, List
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import read_file, write_file, list_files, delete_file, create_directory
from nova.tools.subagent import create_subagent, list_subagents, get_subagent_result, kill_subagent
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
    stop_scheduler
)
# Import heartbeat tools
from nova.tools.heartbeat import (
    start_heartbeat_monitor,
    stop_heartbeat_monitor,
    register_subagent_for_heartbeat,
    unregister_subagent_from_heartbeat,
    get_heartbeat_status,
    get_heartbeat_detailed_status,
    auto_register_active_subagents,
    get_heartbeat_monitor
)
from nova.tools.mcp_registry import mcp_registry
from nova.tools.mcp_tools import add_mcp_server, remove_mcp_server, list_registered_mcp_servers
from nova.tools.specialist_registry import save_specialist_config, list_specialists
from nova.tools.team_manager import run_team_task
from nova.logger import setup_logging
try:
    from agno.tools.mcp import MCPTools, StdioServerParameters, StreamableHTTPClientParams
except ImportError:
    from agno.tools.mcp import MCPTools
    StdioServerParameters = None
    StreamableHTTPClientParams = None
from agno.skills import Skills, LocalSkills

load_dotenv()
setup_logging()

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
    else:
        db = SqliteDb(db_file="/app/data/nova_memory.db")

    repo_skills_path = "/app/data/nova_repo/skills"
    persistent_skills_path = "/app/data/skills"
    os.makedirs(repo_skills_path, exist_ok=True)
    os.makedirs(persistent_skills_path, exist_ok=True)

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
            "- Use the Specialist Registry for complex, recurring roles. Use `create_subagent` for simple, one-off tasks."
        ],
        skills=Skills(loaders=[
            LocalSkills(repo_skills_path),
            LocalSkills(persistent_skills_path)
        ]),
        tools=[
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
            # Heartbeat tools
            start_heartbeat_monitor,
            stop_heartbeat_monitor,
            register_subagent_for_heartbeat,
            unregister_subagent_from_heartbeat,
            get_heartbeat_status,
            get_heartbeat_detailed_status,
            auto_register_active_subagents,
            # MCP tools
            add_mcp_server,
            remove_mcp_server,
            list_registered_mcp_servers,
            # Scheduler tools
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
            # Dynamic Team & Specialist tools
            save_specialist_config,
            list_specialists,
            run_team_task
        ],
        markdown=True,
        add_history_to_context=True,
        update_memory_on_run=True,
        cache_session=True, 
    )

    # Standard Agno Docs MCP
    try:
        if StreamableHTTPClientParams:
            agent.tools.append(MCPTools(
                name="agno_docs",
                server_params=StreamableHTTPClientParams(url="https://docs.agno.com/mcp", timeout=60)
            ))
        else:
            agent.tools.append(MCPTools(name="agno_docs", url="https://docs.agno.com/mcp"))
    except Exception as e:
        print(f"Warning: Failed to load Agno Docs MCP: {e}")

    # Load custom MCPs from Postgres/Registry
    try:
        registered_servers = mcp_registry.list_servers()
        for s in registered_servers:
            try:
                if s['transport'] == "stdio":
                    if StdioServerParameters:
                        server_params = StdioServerParameters(
                            command=s['command'],
                            args=s['args'],
                            env=s['env'] or os.environ.copy()
                        )
                        agent.tools.append(MCPTools(name=s['name'], server_params=server_params))
                    else:
                        cmd = s['command']
                        if s.get('args'):
                            cmd += " " + " ".join(s['args'])
                        agent.tools.append(MCPTools(name=s['name'], command=cmd, env=s.get('env')))
                elif s['transport'] == "streamable-http":
                    if StreamableHTTPClientParams:
                        server_params = StreamableHTTPClientParams(
                            url=s['url'],
                            headers=s.get('env'),
                            timeout=60
                        )
                        agent.tools.append(MCPTools(name=s['name'], server_params=server_params))
                    else:
                        agent.tools.append(MCPTools(name=s['name'], url=s['url']))
            except Exception as e:
                print(f"Warning: Failed to load MCP server {s.get('name')}: {e}")
    except Exception as e:
        print(f"Warning: Failed to list registered MCP servers: {e}")
    
    return agent

if __name__ == "__main__":
    # Initialize scheduler on startup
    from nova.tools.scheduler import initialize_scheduler
    initialize_scheduler()
    
    agent = get_agent()
    print("Nova PM Agent initialized.")
    print("Scheduler started - running in background.")