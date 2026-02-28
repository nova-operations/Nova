"""
Nova Stateful Memory Utility
Provides a protocol for background/recurring tasks to maintain state without custom Python files.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from sqlalchemy import desc
from nova.db.engine import get_session_factory
from nova.db.models.stateful_history import StatefulHistory

logger = logging.getLogger(__name__)

class StatefulMemory:
    """Manages persistent context for jobs and runs."""
    
    @staticmethod
    async def get_history(task_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch the most recent states for a specific task."""
        session = get_session_factory()()
        try:
            results = session.query(StatefulHistory)\
                .filter(StatefulHistory.task_name == task_name)\
                .order_by(desc(StatefulHistory.timestamp))\
                .limit(limit)\
                .all()
            
            return [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "data": r.data,
                    "summary": r.summary
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Failed to fetch history for {task_name}: {e}")
            return []
        finally:
            session.close()

    @staticmethod
    async def save_state(task_name: str, data: Any, summary: Optional[str] = None):
        """Save a new state entry for a task."""
        session = get_session_factory()()
        try:
            new_entry = StatefulHistory(
                task_name=task_name,
                data=data,
                summary=summary,
                timestamp=datetime.utcnow()
            )
            session.add(new_entry)
            session.commit()
            logger.info(f"Saved state for {task_name}")
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save state for {task_name}: {e}")
        finally:
            session.close()

    @staticmethod
    async def get_full_context_prompt(task_name: str, limit: int = 5) -> str:
        """Generates a prompt-ready string of the task's history."""
        history = await StatefulMemory.get_history(task_name, limit)
        if not history:
            return "No previous history found for this task."
        
        prompt_parts = [f"HISTORICAL CONTEXT FOR TASK '{task_name}':"]
        # Reverse because we want oldest to newest for the LLM flow
        for entry in reversed(history):
            part = f"- [{entry['timestamp']}] "
            if entry['summary']:
                part += f"SUMMARY: {entry['summary']} "
            part += f"DATA: {json.dumps(entry['data'])}"
            prompt_parts.append(part)
        
        return "\n".join(prompt_parts)