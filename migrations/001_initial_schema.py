import os
import sys
from sqlalchemy import text, inspect

# Add project root to path
sys.path.insert(0, os.getcwd())

from nova.db.base import Base
from nova.db.engine import get_db_engine
from nova.tools.mcp_registry import MCPServerConfig
from nova.tools.specialist_registry import SpecialistConfig
from nova.tools.scheduler import ScheduledTask


def run_migrations():
    """Run all database migrations."""
    engine = get_db_engine()
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    print(f"Running migrations on {engine.url}...")

    # 1. Specialized Migration: mcp_servers -> nova_mcp_servers
    if "mcp_servers" in tables and "nova_mcp_servers" not in tables:
        print("Migrating old mcp_servers table to nova_mcp_servers...")
        # Create new tables first
        Base.metadata.create_all(engine)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO nova_mcp_servers (name, transport, url, command, args, env) "
                        "SELECT name, transport, url, command, args, env FROM mcp_servers"
                    )
                )
                conn.execute(text("DROP TABLE mcp_servers"))
            print("MCP Metadata migration complete.")
        except Exception as e:
            print(f"MCP Migration failed: {e}")

    # 2. General Table Creation - ensure all models are created
    Base.metadata.create_all(engine)
    
    # Verify key tables exist
    required_tables = [
        "deployment_queue",
        "active_tasks", 
        "task_checkpoints",
        "scheduled_jobs",
        "notification_log",
    ]
    
    for table in required_tables:
        if table in inspector.get_table_names():
            print(f"Table {table} is ready")
        else:
            print(f"WARNING: Table {table} not found!")

    # 3. Schema updates (column additions)
    # Check for team_members in scheduled_tasks
    if "scheduled_tasks" in tables:
        columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]
        if "team_members" not in columns:
            print("Adding 'team_members' column to 'scheduled_tasks'...")
            try:
                with engine.begin() as conn:
                    # SQLite doesn't support JSON type in old versions, but modern ones do via text or JSON
                    conn.execute(
                        text("ALTER TABLE scheduled_tasks ADD COLUMN team_members JSON")
                    )
                print("Added 'team_members' column.")
            except Exception as e:
                print(f"Failed to add team_members column: {e}")

    # 4. Add deployment_pending column to active_tasks if not exists
    # (for tracking when deployment should wait for task)
    if "active_tasks" in tables:
        columns = [c["name"] for c in inspector.get_columns("active_tasks")]
        
        # Check if current_state can store JSON (deployment_pending flag)
        # Since we store it in current_state JSON, no migration needed
        print("Active tasks table ready - using JSON state for deployment flags")

    # 5. Create indexes for better query performance
    try:
        with engine.begin() as conn:
            # Index on active_tasks for project_id lookups
            if "active_tasks" in tables:
                try:
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS idx_active_tasks_project_id "
                        "ON active_tasks(project_id)"
                    ))
                except Exception:
                    pass  # Index might already exist
            
            # Index on task_checkpoints for task_id lookups  
            if "task_checkpoints" in tables:
                try:
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS idx_task_checkpoints_task_id "
                        "ON task_checkpoints(task_id)"
                    ))
                except Exception:
                    pass
                    
    except Exception as e:
        print(f"Index creation note: {e}")

    print("Database is up to date!")
    
    return True


if __name__ == "__main__":
    run_migrations()