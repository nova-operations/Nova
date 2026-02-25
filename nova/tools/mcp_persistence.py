import os
import json
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine, Column, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup using Railway environment variable
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = "sqlite:////app/data/nova_fallback.db"

Base = declarative_base()

class MCPServer(Base):
    __tablename__ = "mcp_servers"
    name = Column(String(255), primary_key=True)
    transport = Column(String(50), nullable=False)
    url = Column(Text, nullable=True)
    command = Column(String(255), nullable=True)
    args = Column(Text) # JSON list
    env = Column(Text)  # JSON dict

class PostgresMemory:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def save_server(self, name: str, transport: str, url: str = None, command: str = None, args: list = None, env: dict = None):
        session = self.Session()
        try:
            mcp = session.query(MCPServer).filter_by(name=name).first()
            if not mcp:
                mcp = MCPServer(name=name)
            
            mcp.transport = transport
            mcp.url = url
            mcp.command = command
            mcp.args = json.dumps(args or [])
            mcp.env = json.dumps(env or {})
            
            session.add(mcp)
            session.commit()
            return f"Saved {name} to PostgreSQL"
        except Exception as e:
            session.rollback()
            return str(e)
        finally:
            session.close()

db = PostgresMemory()
