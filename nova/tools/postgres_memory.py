import os
import json
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine, Column, String, Text, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from agno.utils.log import logger

# Get Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Use a local SQLite as fallback if DB URL is missing (highly unlikely on Railway)
    DATABASE_URL = "sqlite:////app/data/nova_memory_fallback.db"

# Agno-compatible SQLAlchemy Setup
Base = declarative_base()

class MCPServerModel(Base):
    __tablename__ = "mcp_servers"
    name = Column(String(255), primary_key=True)
    command = Column(String(255), nullable=False)
    args = Column(Text)  # JSON string
    env = Column(Text)   # JSON string

class GeneralMemoryModel(Base):
    __tablename__ = "nova_general_memory"
    key = Column(String(255), primary_key=True)
    value = Column(Text)

class PostgresPersistence:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def save_mcp_server(self, name: str, command: str, args: List[str] = None, env: Dict[str, str] = None):
        session = self.Session()
        try:
            args_json = json.dumps(args) if args else "[]"
            env_json = json.dumps(env) if env else "{}"
            
            mcp_entry = session.query(MCPServerModel).filter_by(name=name).first()
            if mcp_entry:
                mcp_entry.command = command
                mcp_entry.args = args_json
                mcp_entry.env = env_json
            else:
                mcp_entry = MCPServerModel(name=name, command=command, args=args_json, env=env_json)
                session.add(mcp_entry)
            
            session.commit()
            return f"MCP Server '{name}' saved to PostgreSQL."
        except Exception as e:
            session.rollback()
            return f"Error saving to Postgres: {str(e)}"
        finally:
            session.close()

    def get_all_mcp_servers(self) -> List[Dict]:
        session = self.Session()
        try:
            servers = session.query(MCPServerModel).all()
            return [{
                "name": s.name,
                "command": s.command,
                "args": json.loads(s.args),
                "env": json.loads(s.env)
            } for s in servers]
        finally:
            session.close()

# Global Persistence Instance
postgres_memory = PostgresPersistence()