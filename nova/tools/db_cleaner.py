"""
Database Cleaner - Destructive operations for history wiping.
"""

import logging
from sqlalchemy import MetaData, text
from nova.db.engine import get_db_engine

logger = logging.getLogger(__name__)


def wipe_all_database_tables():
    """
    Destructive operation: Truncates ALL tables in the database.
    This includes Agno session tables, specialist tables, and application tables.
    """
    engine = get_db_engine()
    metadata = MetaData()

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
                    f'"{row.table_schema}"."{row.table_name}"' for row in rows
                ]

                if not table_list:
                    return "No tables found to wipe."

                # Truncate all tables with CASCADE
                truncate_query = (
                    f"TRUNCATE TABLE {', '.join(table_list)} RESTART IDENTITY CASCADE;"
                )
                conn.execute(text(truncate_query))

                count = len(table_list)
                logger.info(f"Truncated {count} tables across all schemas.")
                return f"Successfully wiped {count} tables (including 'ai' and 'public' schemas)."

            else:
                # SQLite: Reflect and delete (SQLite only has 'main')
                metadata.reflect(bind=engine)
                for table in reversed(metadata.sorted_tables):
                    conn.execute(text(f'DELETE FROM "{table.name}";'))
                    conn.execute(
                        text(f"DELETE FROM sqlite_sequence WHERE name='{table.name}';")
                    )
                conn.execute(text("VACUUM;"))
                count = len(metadata.sorted_tables)
                logger.info(f"Wiped {count} SQLite tables.")
                return f"Successfully wiped {count} tables."

    except Exception as e:
        logger.error(f"Failed to wipe database: {e}")
        return f"Error wiping database: {str(e)}"
