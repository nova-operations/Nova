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

load_dotenv()

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
    if database_url and (database_url.startswith("postgresql://") or database_url.startswith("postgres://")):
        db = PostgresDb(session_table="nova_agent_sessions", db_url=database_url)
    else:
        # Use persistent path for DB if available (e.g. Railway volume at /app/data)
        # If /app/data doesn't exist, fallback to local dir
        db_path = "/app/data/nova_memory.db"
        if not os.path.exists("/app/data"):
            try:
                 os.makedirs("/app/data", exist_ok=True)
            except OSError:
                 # Fallback if we can't create directory
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
            f"Your persistent skills (custom tools/scripts) should be stored in: {skills_path}",
            "You can create new python scripts in the skills directory and execute them to perform complex tasks.",
            "You can commit and push changes to your own GitHub repository using the `push_to_github` tool.",
            "Always be careful when modifying your own code.",
            "If you are asked to improve yourself, analyze the request and make necessary changes."
        ],
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
            pull_latest_changes
        ],
        markdown=True,
        add_history_to_context=True,
        update_memory_on_run=True,
        cache_session=True, 
    )
    
    return agent

if __name__ == "__main__":
    try:
        agent = get_agent()
        print("Agent initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize agent: {e}")
