import asyncio
import uuid
import logging
from typing import Dict, Optional, List
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb
from dotenv import load_dotenv
import os

# Import all tools to give to subagents
from nova.tools.shell import execute_shell_command
from nova.tools.filesystem import read_file, write_file, list_files, delete_file, create_directory
from nova.tools.github_tools import push_to_github, pull_latest_changes

load_dotenv()

# Global dictionary to store running subagents
SUBAGENTS: Dict[str, Dict] = {}

async def run_subagent_task(subagent_id: str, agent: Agent, instruction: str):
    """
    The actual coroutine that runs the subagent.
    """
    try:
        SUBAGENTS[subagent_id]["status"] = "running"
        logging.info(f"Subagent {subagent_id} started running: {instruction}")
        
        # Run the agent asynchronously
        response = await agent.arun(instruction)
        
        SUBAGENTS[subagent_id]["result"] = response.content
        SUBAGENTS[subagent_id]["status"] = "completed"
        logging.info(f"Subagent {subagent_id} completed.")
        
    except Exception as e:
        SUBAGENTS[subagent_id]["status"] = "failed"
        SUBAGENTS[subagent_id]["result"] = str(e)
        logging.error(f"Subagent {subagent_id} failed: {e}")

# Changed to async def to ensure we are in a valid async context
async def create_subagent(name: str, instructions: str, task: str) -> str:
    """
    Creates and starts a subagent in the background.
    
    Args:
        name: A name for the subagent.
        instructions: Instructions for the subagent's persona/behavior.
        task: The specific task or question for the subagent to process.
        
    Returns:
        The ID of the created subagent.
    """
    subagent_id = str(uuid.uuid4())
    
    # Configure the model
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "Error: OPENROUTER_API_KEY not set."

    subagent_model = os.getenv("SUBAGENT_MODEL", "google/gemini-2.0-flash-001")

    model = OpenAIChat(
        id=subagent_model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    
    database_url = os.getenv("DATABASE_URL")
    if database_url and (database_url.startswith("postgresql://") or database_url.startswith("postgres://")):
        db = PostgresDb(table_name="nova_subagent_sessions", db_url=database_url)
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
    
    # Give subagents ALL tools
    tools_list = [
        execute_shell_command,
        read_file,
        write_file,
        list_files,
        delete_file,
        create_directory,
        push_to_github,
        pull_latest_changes,
        create_subagent,
        list_subagents,
        get_subagent_result,
        kill_subagent
    ]

    agent = Agent(
        model=model,
        db=db,
        description=f"Subagent {name}",
        instructions=instructions,
        tools=tools_list,
        markdown=True,
        add_history_to_context=True,
    )
    
    # Create the task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return "Error: No running event loop found. Subagents must be created within an async context."

    t = loop.create_task(run_subagent_task(subagent_id, agent, task))
    
    SUBAGENTS[subagent_id] = {
        "name": name,
        "task_obj": t,
        "agent": agent,
        "status": "starting",
        "result": None,
        "instruction": task
    }
    
    return f"Subagent '{name}' created with ID: {subagent_id}"

def list_subagents() -> str:
    """Lists all managed subagents and their status."""
    if not SUBAGENTS:
        return "No subagents found."
    
    report = []
    for sid, data in SUBAGENTS.items():
        status = data["status"]
        name = data["name"]
        report.append(f"ID: {sid} | Name: {name} | Status: {status}")
    return "\n".join(report)

def get_subagent_result(subagent_id: str) -> str:
    """Retrieves the result of a completed subagent."""
    if subagent_id not in SUBAGENTS:
        return "Error: Subagent not found."
    
    data = SUBAGENTS[subagent_id]
    if data["status"] == "completed":
        return f"Result for {data['name']}:\n{data['result']}"
    elif data["status"] == "failed":
        return f"Subagent {data['name']} failed: {data['result']}"
    else:
        return f"Subagent {data['name']} is currently {data['status']}."

def kill_subagent(subagent_id: str) -> str:
    """Stops a running subagent."""
    if subagent_id not in SUBAGENTS:
        return "Error: Subagent not found."
    
    data = SUBAGENTS[subagent_id]
    if data["status"] in ["running", "starting"]:
        data["task_obj"].cancel()
        data["status"] = "cancelled"
        return f"Subagent {data['name']} cancelled."
    else:
        return f"Subagent {data['name']} is not running (Status: {data['status']})."
