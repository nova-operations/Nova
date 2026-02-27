"""
Task Tracker - Manages active subagent tasks and state

This module tracks multiple active subagents and tasks simultaneously,
preventing state collisions and enabling checkpoint/resume functionality.
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

from sqlalchemy import and_
from sqlalchemy.exc import SQLAlchemyError

from nova.db.engine import get_session_factory
from nova.db.deployment_models import ActiveTask, TaskStatus, TaskCheckpoint

logger = logging.getLogger(__name__)


class TaskTracker:
    """
    Tracks active subagent tasks with concurrency safety.
    Prevents state collisions and provides checkpoint/resume capabilities.
    """

    def __init__(self):
        self._session_factory = get_session_factory()
        self._lock = threading.RLock()
        self._local_cache: Dict[str, Dict[str, Any]] = {}
        self._heartbeat_interval = 30  # seconds

    def _get_session(self):
        """Create a new database session."""
        return self._session_factory()

    @contextmanager
    def _session_scope(self):
        """Provide a transactional scope for database operations."""
        session = self._get_session()
        try:
            yield session
            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            session.close()

    def register_task(
        self,
        task_id: str,
        task_type: str,
        subagent_name: str,
        project_id: Optional[str] = None,
        description: Optional[str] = None,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Register a new active task.
        Returns False if task_id already exists (prevents collision).
        """
        with self._lock:
            # Check for existing task with same ID
            session = self._get_session()
            try:
                existing = (
                    session.query(ActiveTask)
                    .filter(ActiveTask.task_id == task_id)
                    .first()
                )

                if existing:
                    logger.warning(f"Task {task_id} already registered")
                    return False

                task = ActiveTask(
                    task_id=task_id,
                    task_type=task_type,
                    subagent_name=subagent_name,
                    project_id=project_id,
                    description=description,
                    status=TaskStatus.RUNNING,
                    current_state=json.dumps(initial_state) if initial_state else None,
                    progress_percentage=0,
                )

                session.add(task)
                session.commit()

                # Update local cache
                self._local_cache[task_id] = {
                    "status": TaskStatus.RUNNING,
                    "last_update": datetime.utcnow(),
                }

                logger.info(f"Registered task: {task_id} ({subagent_name})")
                return True

            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Failed to register task: {e}")
                return False
            finally:
                session.close()

    def unregister_task(self, task_id: str, final_state: Optional[Dict] = None) -> bool:
        """
        Unregister a task (mark as completed).
        Optionally save final state.
        """
        with self._lock:
            session = self._get_session()
            try:
                task = (
                    session.query(ActiveTask)
                    .filter(ActiveTask.task_id == task_id)
                    .first()
                )

                if not task:
                    logger.warning(f"Task {task_id} not found for unregister")
                    return False

                task.status = TaskStatus.COMPLETED
                if final_state:
                    task.current_state = json.dumps(final_state)

                session.commit()

                # Remove from cache
                self._local_cache.pop(task_id, None)

                logger.info(f"Unregistered task: {task_id}")
                return True

            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Failed to unregister task: {e}")
                return False
            finally:
                session.close()

    def update_heartbeat(self, task_id: str) -> bool:
        """Update the last heartbeat timestamp for a task."""
        session = self._get_session()
        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if not task:
                return False

            task.last_heartbeat = datetime.utcnow()
            session.commit()

            # Update local cache
            if task_id in self._local_cache:
                self._local_cache[task_id]["last_update"] = datetime.utcnow()

            return True
        finally:
            session.close()

    def update_progress(self, task_id: str, progress: int) -> bool:
        """Update task progress percentage."""
        session = self._get_session()
        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if not task:
                return False

            task.progress_percentage = min(100, max(0, progress))
            session.commit()
            return True
        finally:
            session.close()

    def update_state(self, task_id: str, state: Dict[str, Any]) -> bool:
        """Update the current state of a task."""
        session = self._get_session()
        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if not task:
                return False

            task.current_state = json.dumps(state)
            session.commit()
            return True
        finally:
            session.close()

    def get_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the current state of a task."""
        session = self._get_session()
        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if not task or not task.current_state:
                return None

            return json.loads(task.current_state)
        finally:
            session.close()

    def get_active_tasks(
        self,
        project_id: Optional[str] = None,
        subagent_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all active tasks, optionally filtered."""
        session = self._get_session()
        try:
            query = session.query(ActiveTask).filter(
                ActiveTask.status == TaskStatus.RUNNING
            )

            if project_id:
                query = query.filter(ActiveTask.project_id == project_id)
            if subagent_name:
                query = query.filter(ActiveTask.subagent_name == subagent_name)

            tasks = query.all()

            return [
                {
                    "task_id": t.task_id,
                    "task_type": t.task_type,
                    "subagent_name": t.subagent_name,
                    "project_id": t.project_id,
                    "progress": t.progress_percentage,
                    "started_at": t.started_at.isoformat() if t.started_at else None,
                    "last_heartbeat": t.last_heartbeat.isoformat()
                    if t.last_heartbeat
                    else None,
                }
                for t in tasks
            ]
        finally:
            session.close()

    def get_active_count(self) -> int:
        """Get count of active (running) tasks."""
        session = self._get_session()
        try:
            return (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.RUNNING)
                .count()
            )
        finally:
            session.close()

    def create_checkpoint(
        self,
        task_id: str,
        state: Dict[str, Any],
        checkpoint_type: str = "manual",
    ) -> Optional[int]:
        """
        Create a checkpoint for a task state.
        Returns checkpoint ID.
        """
        session = self._get_session()
        try:
            checkpoint = TaskCheckpoint(
                task_id=task_id,
                serialized_state=json.dumps(state),
                checkpoint_type=checkpoint_type,
                is_active=True,
            )

            session.add(checkpoint)
            session.commit()

            logger.info(f"Created checkpoint for task {task_id}: {checkpoint.id}")
            return checkpoint.id

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to create checkpoint: {e}")
            return None
        finally:
            session.close()

    def get_latest_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest active checkpoint for a task."""
        session = self._get_session()
        try:
            checkpoint = (
                session.query(TaskCheckpoint)
                .filter(
                    and_(
                        TaskCheckpoint.task_id == task_id,
                        TaskCheckpoint.is_active,
                    )
                )
                .order_by(TaskCheckpoint.created_at.desc())
                .first()
            )

            if not checkpoint:
                return None

            return {
                "id": checkpoint.id,
                "state": json.loads(checkpoint.serialized_state),
                "type": checkpoint.checkpoint_type,
                "created_at": checkpoint.created_at.isoformat(),
            }
        finally:
            session.close()

    def pause_task(self, task_id: str) -> bool:
        """Pause a running task (for coordinated restart)."""
        session = self._get_session()
        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if not task or task.status != TaskStatus.RUNNING:
                return False

            task.status = TaskStatus.PAUSED

            # Create checkpoint before pausing
            if task.current_state:
                checkpoint = TaskCheckpoint(
                    task_id=task_id,
                    serialized_state=task.current_state,
                    checkpoint_type="pre_deploy",
                    is_active=True,
                )
                session.add(checkpoint)

            session.commit()
            logger.info(f"Paused task: {task_id}")
            return True

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to pause task: {e}")
            return False
        finally:
            session.close()

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task from its checkpoint."""
        session = self._get_session()
        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if not task or task.status != TaskStatus.PAUSED:
                return False

            # Get latest checkpoint
            checkpoint = (
                session.query(TaskCheckpoint)
                .filter(
                    and_(
                        TaskCheckpoint.task_id == task_id,
                        TaskCheckpoint.is_active,
                    )
                )
                .order_by(TaskCheckpoint.created_at.desc())
                .first()
            )

            if checkpoint:
                task.current_state = checkpoint.serialized_state
                # Mark checkpoint as inactive after resume
                checkpoint.is_active = False

            task.status = TaskStatus.RUNNING
            session.commit()

            logger.info(f"Resumed task: {task_id}")
            return True

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to resume task: {e}")
            return False
        finally:
            session.close()

    def pause_all_active(self) -> int:
        """Pause all active tasks (for coordinated restart)."""
        session = self._get_session()
        try:
            active_tasks = (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.RUNNING)
                .all()
            )

            count = 0
            for task in active_tasks:
                task.status = TaskStatus.PAUSED

                if task.current_state:
                    checkpoint = TaskCheckpoint(
                        task_id=task.task_id,
                        serialized_state=task.current_state,
                        checkpoint_type="pre_deploy",
                        is_active=True,
                    )
                    session.add(checkpoint)

                count += 1

            session.commit()
            logger.info(f"Paused {count} active tasks")
            return count

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to pause active tasks: {e}")
            return 0
        finally:
            session.close()

    def cleanup_stale_tasks(self, max_heartbeat_age_seconds: int = 300) -> int:
        """
        Clean up tasks that have stale heartbeats.
        Returns count of cleaned up tasks.
        """
        session = self._get_session()
        try:
            cutoff = datetime.utcnow() - timedelta(seconds=max_heartbeat_age_seconds)

            stale_tasks = (
                session.query(ActiveTask)
                .filter(
                    and_(
                        ActiveTask.status == TaskStatus.RUNNING,
                        ActiveTask.last_heartbeat < cutoff,
                    )
                )
                .all()
            )

            count = 0
            for task in stale_tasks:
                task.status = TaskStatus.FAILED
                count += 1

            session.commit()

            if count > 0:
                logger.warning(f"Cleaned up {count} stale tasks")

            return count

        finally:
            session.close()

    def check_task_exists(self, task_id: str) -> bool:
        """Check if a task is registered."""
        session = self._get_session()
        try:
            return (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
                is not None
            )
        finally:
            session.close()


# Import timedelta for cleanup function
from datetime import timedelta
