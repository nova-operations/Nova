import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agno.db.sqlite import SqliteDb
from agno.db.postgres import PostgresDb


def get_db_url():
    """Returns the database URL from environment or fallback."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        db_path = os.getenv("SQLITE_DB_PATH", "data/nova_memory.db")
        # Ensure data directory exists
        try:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        except OSError:
            db_path = "nova_memory.db"
        database_url = f"sqlite:///{db_path}"
    else:
        # Strip potential quotes from .env
        database_url = database_url.strip('"').strip("'")

    # Standardize postgres prefix
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    return database_url


_engine = None
_session_factory = None


def get_db_engine():
    """Creates/returns a cached SQLAlchemy engine."""
    global _engine
    if _engine is None:
        url = get_db_url()
        if url.startswith("postgresql"):
            _engine = create_engine(
                url, pool_pre_ping=True, pool_size=5, max_overflow=10, echo=False
            )
        else:
            _engine = create_engine(url)
    return _engine


def get_session_factory():
    """Returns a cached sessionmaker instance."""
    global _session_factory
    if _session_factory is None:
        engine = get_db_engine()
        _session_factory = sessionmaker(bind=engine)
    return _session_factory


def get_agno_db(session_table: str):
    """Returns an Agno-compatible DB instance."""
    url = get_db_url()
    if url.startswith("postgresql"):
        return PostgresDb(session_table=session_table, db_url=url)

    # Extract path for SQLite
    db_file = url.replace("sqlite:///", "")
    return SqliteDb(db_file=db_file)
