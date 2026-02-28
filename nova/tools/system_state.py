"""
System State Tool for Nova

Provides a comprehensive overview of Nova's current runtime state,
including active projects, running subagents, and active scheduled jobs (watchers).
"""

import json
import logging
from sqlalchemy.orm import sessionmaker

from nova.db.engine import get_session_factory
from nova.db.deployment_models import (
    ProjectContext,
    ActiveTask,
    TaskStatus as ActiveTaskStatus,
)
from nova.tools.scheduler import ScheduledTask, TaskType
from nova.tools.context_optimizer import wrap_tool_output_optimization

logger = logging.getLogger(__name__)


@wrap_tool_output_optimization
def get_system_state() -> str:
    """
    Returns a unified summary of Nova's current system state.
    This includes:
    1. The actively managed project.
    2. Any currently running subagents or tasks.
    3. All active scheduled background tasks (including watchers).

    Returns:
        A Markdown-formatted string summarizing the entire system state.
    """
    session_factory = get_session_factory()
    session = session_factory()

    parts = ["# 🌌 Nova System State Overview\n"]

    try:
        # 1. Active Project
        project = session.query(ProjectContext).filter(ProjectContext.is_active).first()
        if project:
            parts.append(f"## 📁 Active Project: **{project.name}**")
            parts.append(f"- **Path:** `{project.absolute_path}`")
            if project.git_remote:
                parts.append(f"- **Git Remote:** `{project.git_remote}`")
        else:
            parts.append("## 📁 Active Project: **None** (System running globally)")

        parts.append("\n---\n")

        # 2. Running Subagents / Active Tasks
        active_tasks = (
            session.query(ActiveTask)
            .filter(ActiveTask.status == ActiveTaskStatus.RUNNING)
            .all()
        )
        parts.append(f"## 🤖 Running Subagents ({len(active_tasks)})")
        if active_tasks:
            for task in active_tasks:
                sub_name = task.subagent_name or "Unknown"
                parts.append(
                    f"- **[{task.task_id}]** `{sub_name}`: {task.description or 'No description'}"
                )
        else:
            parts.append("- No active subagents or tasks currently running.")

        parts.append("\n---\n")

        # 3. Scheduled Jobs & Watchers
        # Import TaskStatus from scheduler explicitly to prevent enum collisions
        from nova.tools.scheduler import TaskStatus as SchedTaskStatus

        sched_tasks = (
            session.query(ScheduledTask)
            .filter(ScheduledTask.status == SchedTaskStatus.ACTIVE)
            .all()
        )
        parts.append(f"## ⏱️ Active Scheduled Jobs & Watchers ({len(sched_tasks)})")

        watchers = [t for t in sched_tasks if t.task_type == TaskType.WATCHER]
        others = [t for t in sched_tasks if t.task_type != TaskType.WATCHER]

        if not sched_tasks:
            parts.append("- No active scheduled jobs.")
        else:
            if watchers:
                parts.append("### 👀 Background Watchers:")
                for w in watchers:
                    parts.append(
                        f"- **{w.task_name}** (Schedule: `{w.schedule}`) -> Last Run: {w.last_status or 'Never'}"
                    )

            if others:
                parts.append("### 🔄 General Jobs:")
                for t in others:
                    # t.task_type might be a string from the DB or an Enum
                    type_str = (
                        t.task_type.value
                        if hasattr(t.task_type, "value")
                        else str(t.task_type)
                    )
                    parts.append(
                        f"- **{t.task_name}** ({type_str}) -> Schedule: `{t.schedule}`"
                    )

    except Exception as e:
        logger.error(f"Error getting system state: {e}")
        parts.append(f"\n❌ **Error retrieving system state:** {str(e)}")
    finally:
        session.close()

    return "\n".join(parts)
