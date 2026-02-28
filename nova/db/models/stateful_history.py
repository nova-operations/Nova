from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from nova.db.base import Base

class StatefulHistory(Base):
    """Stores persistent state for recurring tasks and agent memory."""
    __tablename__ = "stateful_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String(255), nullable=False, index=True)
    state_key = Column(String(255), nullable=False, default="default") 
    data = Column(JSON, nullable=False) # The actual state context
    timestamp = Column(DateTime, default=datetime.utcnow)
    summary = Column(Text, nullable=True) # Optional human-readable summary

    def __repr__(self):
        return f"<StatefulHistory(task='{self.task_name}', key='{self.state_key}', time='{self.timestamp}')>"