"""
Queue Manager - Core deployment queue logic

This module handles priority-based deployment queuing with
concurrency safety and graceful worker coordination.
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
from enum import Enum

from sqlalchemy import and_, or_, case
from sqlalchemy.exc import SQLAlchemyError

from nova.db.engine import get_session_factory
from nova.db.deployment_models import (
    DeploymentQueue,
    DeploymentType,
    QueuePriority,
    QueueStatus,
    ActiveTask,
    TaskStatus,
    TaskCheckpoint,
    ScheduledJob,
    NotificationLog,
)

logger = logging.getLogger(__name__)


class QueueManager:
    """
    Manages the deployment queue with priority handling.
    Ensures concurrency-safe operations and coordinates with active workers.
    """

    # Destructive actions that require worker coordination
    DESTRUCTIVE_ACTIONS = {DeploymentType.REDEPLOY, DeploymentType.RESTART}

    def __init__(self):
        self._session_factory = get_session_factory()
        self._lock = threading.RLock()
        self._worker_check_callback: Optional[Callable] = None
        self._notification_callback: Optional[Callable] = None

    def set_worker_check_callback(self, callback: Callable[[], int]):
        """Set callback to check number of active workers."""
        self._worker_check_callback = callback

    def set_notification_callback(self, callback: Callable[[str, str], None]):
        """Set callback for sending notifications to users."""
        self._notification_callback = callback

    def _get_session(self):
        """Create a new database session."""
        return self._session_factory()

    def _is_destructive_action(self, deployment_type: DeploymentType) -> bool:
        """Check if deployment type is destructive."""
        return deployment_type in self.DESTRUCTIVE_ACTIONS

    def _get_priority_sort_key(self):
        """Get a sortable priority expression using case with integer values."""
        # Use integer values in case for cross-database compatibility
        return case(
            {
                QueuePriority.LOW: 1,
                QueuePriority.NORMAL: 2,
                QueuePriority.HIGH: 3,
                QueuePriority.CRITICAL: 4,
            },
            value=DeploymentQueue.priority,
        )

    def add_to_queue(
        self,
        deployment_type: DeploymentType,
        target_service: str,
        requested_by: Optional[str] = None,
        reason: Optional[str] = None,
        priority: Optional[QueuePriority] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> int:
        """
        Add a deployment to the queue.
        Destructive actions get automatic high priority.
        Returns the queue item ID.
        """
        with self._lock:
            session = self._get_session()
            try:
                # Auto-upgrade priority for destructive actions
                if priority is None:
                    if self._is_destructive_action(deployment_type):
                        priority = QueuePriority.HIGH
                    else:
                        priority = QueuePriority.NORMAL

                queue_item = DeploymentQueue(
                    deployment_type=deployment_type,
                    target_service=target_service,
                    priority=priority,
                    requested_by=requested_by,
                    reason=reason,
                    scheduled_at=scheduled_at,
                    requires_state_pause=self._is_destructive_action(deployment_type),
                    status=QueueStatus.PENDING,
                )

                session.add(queue_item)
                session.commit()
                queue_id = queue_item.id

                logger.info(
                    f"Added deployment to queue: {deployment_type.value} "
                    f"for {target_service} (priority: {priority.name}, id: {queue_id})"
                )

                # Send notification if destructive action
                if self._notification_callback and self._is_destructive_action(
                    deployment_type
                ):
                    self._send_notification(
                        user_id=requested_by or "system",
                        message_type="queue_added",
                        message=f"Deployment '{deployment_type.value}' for {target_service} "
                        f"added to queue (Priority: {priority.name}). "
                        f"Will wait for active tasks to complete before execution.",
                    )

                return queue_id

            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Failed to add deployment to queue: {e}")
                raise
            finally:
                session.close()

    def get_next_pending(self) -> Optional[DeploymentQueue]:
        """
        Get the next pending deployment item from queue.
        Items are ordered by priority (descending) then by creation time.
        Uses in-memory sorting for reliability.
        """
        with self._lock:
            session = self._get_session()
            try:
                items = (
                    session.query(DeploymentQueue)
                    .filter(DeploymentQueue.status == QueueStatus.PENDING)
                    .filter(
                        or_(
                            DeploymentQueue.scheduled_at.is_(None),
                            DeploymentQueue.scheduled_at <= datetime.utcnow(),
                        )
                    )
                    .all()
                )

                # Sort in memory by priority value (most reliable)
                if items:
                    items.sort(
                        key=lambda x: (x.priority.value, x.created_at), reverse=True
                    )
                    return items[0]

                return None
            finally:
                session.close()

    def get_queue_status(self) -> List[Dict[str, Any]]:
        """Get current queue status for all items."""
        with self._lock:
            session = self._get_session()
            try:
                items = session.query(DeploymentQueue).all()

                # Sort in memory by priority value
                items.sort(key=lambda x: (x.priority.value, x.created_at), reverse=True)

                return [
                    {
                        "id": item.id,
                        "deployment_type": item.deployment_type.value,
                        "target_service": item.target_service,
                        "priority": item.priority.name,
                        "status": item.status.value,
                        "created_at": item.created_at.isoformat()
                        if item.created_at
                        else None,
                        "requested_by": item.requested_by,
                    }
                    for item in items
                ]
            finally:
                session.close()

    def update_status(
        self,
        queue_id: int,
        status: QueueStatus,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update the status of a queue item."""
        with self._lock:
            session = self._get_session()
            try:
                item = (
                    session.query(DeploymentQueue)
                    .filter(DeploymentQueue.id == queue_id)
                    .first()
                )

                if not item:
                    logger.warning(f"Queue item {queue_id} not found")
                    return False

                item.status = status

                if status == QueueStatus.PROCESSING:
                    item.started_at = datetime.utcnow()
                elif status in (
                    QueueStatus.COMPLETED,
                    QueueStatus.FAILED,
                    QueueStatus.CANCELLED,
                ):
                    item.completed_at = datetime.utcnow()

                if error_message:
                    item.error_message = error_message

                session.commit()
                logger.info(f"Updated queue item {queue_id} status to {status.value}")
                return True

            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Failed to update queue status: {e}")
                return False
            finally:
                session.close()

    def cancel_queue_item(self, queue_id: int) -> bool:
        """Cancel a pending queue item."""
        return self.update_status(queue_id, QueueStatus.CANCELLED)

    def get_active_worker_count(self) -> int:
        """Get the count of currently active workers/tasks."""
        if self._worker_check_callback:
            return self._worker_check_callback()

        # Fallback: check database
        session = self._get_session()
        try:
            return (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.RUNNING)
                .count()
            )
        finally:
            session.close()

    def check_can_proceed(self, queue_id: int) -> tuple[bool, str]:
        """
        Check if a queue item can proceed.
        For destructive actions, waits until workers are idle.
        """
        session = self._get_session()
        try:
            item = (
                session.query(DeploymentQueue)
                .filter(DeploymentQueue.id == queue_id)
                .first()
            )

            if not item:
                return False, "Queue item not found"

            if item.status != QueueStatus.PENDING:
                return False, f"Item status is {item.status.value}, not pending"

            # Check if destructive action needs worker coordination
            if item.requires_state_pause:
                worker_count = self.get_active_worker_count()

                if worker_count > 0:
                    # Update status to waiting
                    item.status = QueueStatus.WAITING_FOR_WORKERS
                    session.commit()

                    return (
                        False,
                        f"Waiting for {worker_count} active worker(s) to complete",
                    )

            return True, "Can proceed"

        finally:
            session.close()

    def retry_failed_items(self) -> int:
        """Retry failed queue items that haven't exceeded max retries."""
        session = self._get_session()
        try:
            failed_items = (
                session.query(DeploymentQueue)
                .filter(
                    and_(
                        DeploymentQueue.status == QueueStatus.FAILED,
                        DeploymentQueue.retry_count < DeploymentQueue.max_retries,
                    )
                )
                .all()
            )

            count = 0
            for item in failed_items:
                item.status = QueueStatus.PENDING
                item.retry_count += 1
                item.error_message = None
                count += 1

            session.commit()
            return count

        finally:
            session.close()

    def _send_notification(self, user_id: str, message_type: str, message: str):
        """Send notification to user."""
        if not self._notification_callback:
            return

        try:
            # Log notification
            session = self._get_session()
            try:
                notif = NotificationLog(
                    user_id=user_id,
                    message_type=message_type,
                    message=message,
                )
                session.add(notif)
                session.commit()
            finally:
                session.close()

            # Send via callback
            self._notification_callback(user_id, message)

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
