import os
import json
from typing import List, Dict, Optional
from sqlalchemy import create_engine, Column, String, Text, inspect, JSON
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
    args = Column(JSON, default=list) 
    url = Column(String(255))
    env = Column(JSON, default=dict)

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
        self._migrate_old_table()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _migrate_old_table(self):
        """Migrates data from old mcp_servers to new nova_mcp_servers if it exists."""
        from sqlalchemy import text, inspect
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        
        if "mcp_servers" in tables and "nova_mcp_servers" not in tables:
            print("🚀 Migrating old mcp_servers table to nova_mcp_servers...")
            try:
                # Create the new table first
                Base.metadata.create_all(self.engine)
                with self.engine.begin() as conn:
                    # Copy data
                    conn.execute(text("INSERT INTO nova_mcp_servers (name, transport, url, command, args, env) "
                                      "SELECT name, transport, url, command, args, env FROM mcp_servers"))
                    # Drop old table
                    conn.execute(text("DROP TABLE mcp_servers"))
                print("✅ Migration complete.")
            except Exception as e:
                print(f"⚠️ Migration failed (probably already done or empty): {e}")
        elif "mcp_servers" in tables:
             # Just drop it if both exist and we are sure
             try:
                 with self.engine.begin() as conn:
                     conn.execute(text("DROP TABLE IF EXISTS mcp_servers"))
             except: pass

    def register_server(self, name: str, transport: str = "stdio", command: str = None, args: List[str] = None, url: str = None, env: Dict[str, str] = None) -> str:
        session = self.Session()
        try:
            config = session.query(MCPServerConfig).filter_by(name=name).first()
            if not config:
                config = MCPServerConfig(name=name)
                session.add(config)
            
            config.transport = transport
            config.command = command
            config.args = args if args else []
            config.url = url
            config.env = env if env else {}
            
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
                "args": s.args if isinstance(s.args, list) else json.loads(s.args or "[]"),
                "url": s.url,
                "env": s.env if isinstance(s.env, dict) else json.loads(s.env or "{}")
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
