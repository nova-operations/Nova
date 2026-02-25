import os
import json
from typing import List, Dict, Optional
from sqlalchemy import create_engine, Column, String, Text, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from nova.logger import setup_logging

setup_logging()

Base = declarative_base()

class MCPServerConfig(Base):
    __tablename__ = "nova_mcp_servers"
    name = Column(String(255), primary_key=True)
    transport = Column(String(50), default="stdio") # stdio or streamable-http
    command = Column(String(255))
    args = Column(Text) # JSON string
    url = Column(String(255))
    env = Column(Text) # JSON string

class MCPRegistry:
    def __init__(self):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            db_path = "/app/data/nova_memory.db"
            if not os.path.exists("/app/data"):
                db_path = "nova_memory.db"
            database_url = f"sqlite:///{db_path}"
        
        # SQLAlchemy handles postgres:// vs postgresql:// for us if we use the right driver,
        # but for consistency we ensure it.
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
            
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def register_server(self, name: str, transport: str = "stdio", command: str = None, args: List[str] = None, url: str = None, env: Dict[str, str] = None) -> str:
        session = self.Session()
        try:
            config = session.query(MCPServerConfig).filter_by(name=name).first()
            if not config:
                config = MCPServerConfig(name=name)
                session.add(config)
            
            config.transport = transport
            config.command = command
            config.args = json.dumps(args) if args else "[]"
            config.url = url
            config.env = json.dumps(env) if env else "{}"
            
            session.commit()
            return f"MCP Server '{name}' registered successfully."
        except Exception as e:
            session.rollback()
            return f"Error registering MCP server: {e}"
        finally:
            session.close()

    def list_servers(self) -> List[Dict]:
        session = self.Session()
        try:
            servers = session.query(MCPServerConfig).all()
            return [{
                "name": s.name,
                "transport": s.transport,
                "command": s.command,
                "args": json.loads(s.args),
                "url": s.url,
                "env": json.loads(s.env)
            } for s in servers]
        finally:
            session.close()

    def remove_server(self, name: str) -> str:
        session = self.Session()
        try:
            config = session.query(MCPServerConfig).filter_by(name=name).first()
            if config:
                session.delete(config)
                session.commit()
                return f"MCP Server '{name}' removed."
            return f"MCP Server '{name}' not found."
        finally:
            session.close()

# Global Registry
mcp_registry = MCPRegistry()
