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
        # Reflect all tables from the database
        metadata.reflect(bind=engine)

        with engine.begin() as conn:
            # Disable foreign key checks for the session if postgres
            if engine.url.drivername.startswith("postgresql"):
                # Truncate all tables with CASCADE
                table_names = [table.name for table in metadata.sorted_tables]
                if not table_names:
                    return "No tables found to wipe."

                # Truncate multiple tables in one command
                formatted_names = ", ".join([f'"{name}"' for name in table_names])
                truncate_query = (
                    f"TRUNCATE TABLE {formatted_names} RESTART IDENTITY CASCADE;"
                )
                conn.execute(text(truncate_query))
                logger.info(f"Truncated {len(table_names)} tables: {table_names}")
            else:
                # SQLite doesn't support TRUNCATE CASCADE easily, we just delete and vacuum
                for table in reversed(metadata.sorted_tables):
                    conn.execute(text(f'DELETE FROM "{table.name}";'))
                    conn.execute(
                        text(f"DELETE FROM sqlite_sequence WHERE name='{table.name}';")
                    )
                conn.execute(text("VACUUM;"))
                logger.info(f"Wiped {len(metadata.sorted_tables)} SQLite tables.")

        return f"Successfully wiped {len(metadata.sorted_tables)} tables."
    except Exception as e:
        logger.error(f"Failed to wipe database: {e}")
        return f"Error wiping database: {str(e)}"
