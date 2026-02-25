import os
from typing import Optional
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import read_file, write_file, list_files, delete_file, create_directory
from nova.tools.subagent import create_subagent, list_subagents, get_subagent_result, kill_subagent
from nova.tools.github_tools import push_to_github, pull_latest_changes
from nova.tools.mcp_registry import mcp_registry
from nova.tools.mcp_tools import add_mcp_server, remove_mcp_server, list_registered_mcp_servers
from nova.logger import setup_logging
from agno.tools.mcp import MCPTools
from agno.skills import Skills, LocalSkills

load_dotenv()
setup_logging()

def get_agent(model_id: Optional[str] = None):
    """
    Creates and returns a configured Agno Agent.
    """
    if model_id is None:
        model_id = os.getenv("AGENT_MODEL", "google/gemini-2.0-flash-001")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Warning: OPENROUTER_API_KEY environment variable not set. Agent might fail if model is used.")
    
    # Configure the model to use OpenRouter
    model = OpenAIChat(
        id=model_id,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        db = PostgresDb(session_table="nova_agent_sessions", db_url=database_url)
    else:
        db_path = "/app/data/nova_memory.db"
        if not os.path.exists("/app/data"):
            db_path = "nova_memory.db"
        db = SqliteDb(db_file=db_path)

    # Define persistent skills directory
    skills_path = "/app/data/skills"
    if not os.path.exists("/app/data"):
        skills_path = os.path.join(os.getcwd(), "skills")
    os.makedirs(skills_path, exist_ok=True)

    agent = Agent(
        model=model,
        db=db,
        description="I am Nova, a self-improving AI agent running on Railway.",
        instructions=[
            "You are an advanced AI agent capable of self-improvement.",
            "You have access to tools that allow you to interact with your environment.",
            "You can execute shell commands and modify files.",
            "Your workspace is in `/app/data/nova_repo`. This is where your source code is mirrored and where you should make changes.",
            "You have access to the Agno MCP Server (`agno_docs`) which provides documentation and tools for the Agno framework. Always use it to look up the best ways to implement/improve your logic.",
            f"Your skills are stored in: {skills_path}. You can create new skills by creating subdirectories here with a `SKILL.md` file.",
            "Each skill directory should contain:",
            "  1. `SKILL.md`: Instructions with YAML frontmatter (name, description).",
            "  2. `scripts/`: Python scripts or other tools.",
            "  3. `references/`: Supporting documentation.",
            "You can use the `get_skill_instructions`, `get_skill_script`, and `get_skill_reference` tools to discover and use these skills.",
            "You can commit and push changes to your own GitHub repository using the `push_to_github` tool. This will trigger a redeployment.",
            "Always be careful when modifying your own code. Test your changes locally using `python smoke_test.py` before pushing.",
            "If you are asked to improve yourself, analyze the codebase in `/app/data/nova_repo`, make necessary changes, and push them."
        ],
        skills=Skills(loaders=[LocalSkills(skills_path)]),
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
            add_mcp_server,
            remove_mcp_server,
            list_registered_mcp_servers
        ],
        markdown=True,
        add_history_to_context=True,
        update_memory_on_run=True,
        cache_session=True, 
    )

    # Dynamically add MCP tools from registry
    try:
        # Add default Agno MCP tools for self-improvement guidance
        agent.tools.append(MCPTools(
            name="agno_docs",
            transport="streamable-http",
            url="https://docs.agno.com/mcp"
        ))

        registered_servers = mcp_registry.list_servers()
        for s in registered_servers:
            mcp_kwargs = {
                "name": s['name'],
                "transport": s['transport']
            }
            if s['transport'] == "stdio":
                mcp_kwargs["command"] = s['command']
                mcp_kwargs["args"] = s['args']
                mcp_kwargs["env"] = s['env']
            elif s['transport'] == "streamable-http":
                mcp_kwargs["url"] = s['url']
            
            mcp_tool = MCPTools(**mcp_kwargs)
            agent.tools.append(mcp_tool)
    except Exception as e:
        print(f"Error loading MCP tools: {e}")
    
    return agent

if __name__ == "__main__":
    try:
        agent = get_agent()
        print("Agent initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize agent: {e}")
