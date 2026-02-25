import json
from typing import List, Dict
from sqlalchemy import Column, String, JSON
from nova.db.base import Base
from nova.db.engine import get_session_factory
from nova.logger import setup_logging

setup_logging()


class MCPServerConfig(Base):
    __tablename__ = "nova_mcp_servers"
    name = Column(String(255), primary_key=True)
    transport = Column(String(50), default="stdio")  # stdio or streamable-http
    command = Column(String(255))
    args = Column(JSON, default=list)
    url = Column(String(255))
    env = Column(JSON, default=dict)


class MCPRegistry:
    def __init__(self):
        self.Session = get_session_factory()

    def register_server(
        self,
        name: str,
        transport: str = "stdio",
        command: str = None,
        args: List[str] = None,
        url: str = None,
        env: Dict[str, str] = None,
    ) -> str:
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
            return [
                {
                    "name": s.name,
                    "transport": s.transport,
                    "command": s.command,
                    "args": (
                        s.args
                        if isinstance(s.args, list)
                        else json.loads(s.args or "[]")
                    ),
                    "url": s.url,
                    "env": (
                        s.env if isinstance(s.env, dict) else json.loads(s.env or "{}")
                    ),
                }
                for s in servers
            ]
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
