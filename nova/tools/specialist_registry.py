import os
import json
from typing import List, Dict, Optional
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()

class SpecialistConfig(Base):
    """Configuration for a reusable specialist agent."""
    __tablename__ = "specialist_configs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    role = Column(Text, nullable=False)
    instructions = Column(Text, nullable=False)
    model = Column(String(255), default="google/gemini-2.0-flash-001")
    tools = Column(JSON, default=list)  # List of tool names the agent should have
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def get_db_engine():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return create_engine("sqlite:////app/data/nova_specialists.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return create_engine(database_url)

# Initialization
engine = get_db_engine()
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

def save_specialist_config(name: str, role: str, instructions: str, model: str = None, tools: List[str] = None) -> str:
    """Save or update a specialist configuration in the database."""
    session = SessionLocal()
    try:
        config = session.query(SpecialistConfig).filter(SpecialistConfig.name == name).first()
        if not config:
            config = SpecialistConfig(name=name)
            session.add(config)
        
        config.role = role
        config.instructions = instructions
        if model: config.model = model
        if tools is not None: config.tools = tools
        
        session.commit()
        return f"✅ Specialist '{name}' saved to registry."
    except Exception as e:
        session.rollback()
        return f"❌ Error saving specialist: {e}"
    finally:
        session.close()

def get_specialist_config(name: str) -> Optional[Dict]:
    """Retrieve a specialist configuration."""
    session = SessionLocal()
    try:
        config = session.query(SpecialistConfig).filter(SpecialistConfig.name == name).first()
        if config:
            return {
                "name": config.name,
                "role": config.role,
                "instructions": config.instructions,
                "model": config.model,
                "tools": config.tools
            }
        return None
    finally:
        session.close()

def list_specialists() -> str:
    """List all registered specialists."""
    session = SessionLocal()
    try:
        configs = session.query(SpecialistConfig).all()
        if not configs:
            return "No specialists registered."
        
        lines = ["📋 **Specialist Registry**", ""]
        for c in configs:
            lines.append(f"**{c.name}** ({c.model})")
            lines.append(f"  Role: {c.role}")
            lines.append("")
        return "\n".join(lines)
    finally:
        session.close()
