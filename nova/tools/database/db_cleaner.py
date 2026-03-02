"""
Database Cleaner - Destructive operations for history wiping.
"""

import logging
from sqlalchemy import MetaData, text
from nova.db.engine import get_db_engine

logger = logging.getLogger(__name__)


def wipe_all_database_tables(force_all: bool = False):
    """
    Destructive operation: Truncates conversation history and session tables.
    PRESERVES system config tables by default, unless force_all=True.
    """
    engine = get_db_engine()
    metadata = MetaData()

    # Tables that are preserved by default but wiped if force_all=True
    PROTECTED_TABLES = {
        "specialist_configs",
        "scheduled_tasks",
        "apscheduler_jobs",
        "deployment_queue",  # Railway deployment state
    } if not force_all else set()

    try:
        with engine.begin() as conn:
            # Postgres: Discover all tables across all non-system schemas
            if engine.url.drivername.startswith("postgresql"):
                # Query information_schema for all user tables
                query = text(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_type = 'BASE TABLE'
                    AND table_schema NOT IN ('information_schema', 'pg_catalog')
                """
                )
                rows = conn.execute(query).fetchall()

                table_list = [
                    f'"{row.table_schema}"."{row.table_name}"'
                    for row in rows
                    if row.table_name not in PROTECTED_TABLES
                ]

                if not table_list:
                    return "No tables found to wipe."

                # Truncate all tables with CASCADE
                truncate_query = (
                    f"TRUNCATE TABLE {', '.join(table_list)} RESTART IDENTITY CASCADE;"
                )
                conn.execute(text(truncate_query))

                count = len(table_list)
                logger.info(f"Truncated {count} tables across all schemas (force_all={force_all}).")
                msg = f"Successfully wiped {count} tables."
                if not force_all:
                    msg += " (preserved: specialist_configs, scheduled_tasks)"
                else:
                    msg += " (FACTORY RESET COMPLETE)"
                return msg

            else:
                # SQLite: Reflect and delete (SQLite only has 'main')
                metadata.reflect(bind=engine)
                wiped_count = 0
                for table in reversed(metadata.sorted_tables):
                    if not force_all and table.name in PROTECTED_TABLES:
                        continue
                    conn.execute(text(f'DELETE FROM "{table.name}";'))
                    conn.execute(
                        text(f"DELETE FROM sqlite_sequence WHERE name='{table.name}';")
                    )
                    wiped_count += 1
                conn.execute(text("VACUUM;"))
                logger.info(f"Wiped {wiped_count} SQLite tables (force_all={force_all}).")
                return f"Successfully wiped {wiped_count} tables."

    except Exception as e:
        logger.error(f"Failed to wipe database: {e}")
        return f"Error wiping database: {str(e)}"
