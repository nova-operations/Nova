"""
Migration script to create scheduler tables in PostgreSQL.

Run this script once to initialize the database schema for the scheduler system.

Usage:
    python migrations/scheduler_tables.py

Environment variables required:
    - DATABASE_URL: PostgreSQL connection string
"""

import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_migration():
    """Create scheduler tables in the database."""
    from nova.tools.scheduler import init_db, Base, ScheduledTask, get_db_engine

    print("Initializing scheduler database tables...")

    try:
        # Create all tables
        engine = init_db()
        print("✅ Database tables created successfully!")

        # Verify tables exist
        from sqlalchemy import inspect

        inspector = inspect(engine)
        tables = inspector.get_table_names()

        print(f"\nTables in database: {tables}")

        if "scheduled_tasks" in tables:
            print("✅ 'scheduled_tasks' table confirmed")
        else:
            print("❌ 'scheduled_tasks' table not found")

        if "apscheduler_jobs" in tables:
            print("✅ 'apscheduler_jobs' table confirmed")
        else:
            print("❌ 'apscheduler_jobs' table not found")

        print("\n✨ Migration complete!")
        return True

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False


if __name__ == "__main__":
    # Load environment variables
    load_dotenv()

    # Check for DATABASE_URL
    if not os.getenv("DATABASE_URL"):
        print("Error: DATABASE_URL not set in environment")
        print("\nPlease set DATABASE_URL in your .env file or environment")
        print("Example: postgresql://user:password@localhost:5432/nova")
        sys.exit(1)

    # Run migration
    success = run_migration()
    sys.exit(0 if success else 1)
