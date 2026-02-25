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

    print(f"🚀 Running migrations on {engine.url}...")

    # 1. Specialized Migration: mcp_servers -> nova_mcp_servers
    if "mcp_servers" in tables and "nova_mcp_servers" not in tables:
        print("📦 Migrating old mcp_servers table to nova_mcp_servers...")
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
            print("✅ MCP Metadata migration complete.")
        except Exception as e:
            print(f"⚠️ MCP Migration failed: {e}")

    # 2. General Table Creation
    Base.metadata.create_all(engine)
    print("✅ All tables ensured (create_all).")

    # 3. Schema updates (column additions)
    # Check for team_members in scheduled_tasks
    if "scheduled_tasks" in tables:
        columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]
        if "team_members" not in columns:
            print("🔧 Adding 'team_members' column to 'scheduled_tasks'...")
            try:
                with engine.begin() as conn:
                    # SQLite doesn't support JSON type in old versions, but modern ones do via text or JSON
                    conn.execute(
                        text("ALTER TABLE scheduled_tasks ADD COLUMN team_members JSON")
                    )
                print("✅ Added 'team_members' column.")
            except Exception as e:
                print(f"⚠️ Failed to add team_members column: {e}")

    print("✨ Database is up to date!")


if __name__ == "__main__":
    run_migrations()
