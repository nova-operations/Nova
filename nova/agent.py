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
from nova.tools.mcp_registry import mcp_registry
from nova.tools.mcp_tools import add_mcp_server, remove_mcp_server, list_registered_mcp_servers
from nova.logger import setup_logging
from agno.tools.mcp import MCPTools
from agno.skills import Skills, LocalSkills

load_dotenv()
setup_logging()

def get_agent(model_id: Optional[str] = None):
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
            "2. **Heartbeat Protocol**: While subagents are working, you MUST provide 'Heartbeat Updates' to the user. Do not wait for complete silence.",
            "3. **Monitor Progress**: Use `list_subagents` and `get_subagent_result` to track the state of your team.",
            "4. **Synthesis**: Once subagents complete their tasks, gather their outputs and provide a final synthesized response to the user.",

            "## TOOLS & SKILLS:",
            "- You have full access to the filesystem and shell.",
            "- You use PostgreSQL for persistent memory of MCP configurations and agent states.",
            "- You use Agno MCP tools to fetch the latest documentation and remain 'state-of-the-art'.",

            "## COLLABORATION:",
            "- Always treat subagents as your team members. Provide them with clear, detailed instructions.",
            "- If a subagent fails, analyze the error and either retry or spawn a different specialist."
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
            add_mcp_server,
            remove_mcp_server,
            list_registered_mcp_servers
        ],
        markdown=True,
        add_history_to_context=True,
        update_memory_on_run=True,
        cache_session=True, 
    )

    # Initialize MCP Registry and Tools
    try:
        # Standard Agno Docs MCP
        agent.tools.append(MCPTools(name="agno_docs", transport="streamable-http", url="https://docs.agno.com/mcp"))

        # Load custom MCPs from Postgres/Registry
        registered_servers = mcp_registry.list_servers()
        for s in registered_servers:
            mcp_kwargs = {"name": s['name'], "transport": s['transport']}
            if s['transport'] == "stdio":
                mcp_kwargs.update({"command": s['command'], "args": s['args'], "env": s['env']})
            elif s['transport'] == "streamable-http":
                mcp_kwargs["url"] = s['url']
                # Include headers if they represent auth
                if s.get('env'): mcp_kwargs['env'] = s['env']
            
            agent.tools.append(MCPTools(**mcp_kwargs))
    except Exception as e:
        print(f"Error loading MCP tools: {e}")
    
    return agent

if __name__ == "__main__":
    agent = get_agent()
    print("Nova PM Agent initialized.")