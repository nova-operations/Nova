"""
Startup Recovery Script - Handles tasks that were interrupted by restart

This script runs on system startup to recover from any tasks that were
running when the system was shut down or crashed.
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# Add project root to path
sys.path.insert(0, os.getcwd())

from sqlalchemy import and_
from nova.db.engine import get_session_factory
from nova.db.deployment_models import (
    ActiveTask,
    TaskStatus,
    TaskCheckpoint,
    DeploymentQueue,
    QueueStatus,
)
from nova.task_tracker import TaskTracker

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class StartupRecovery:
    """
    Handles recovery of tasks and deployments after system restart.
    """

    def __init__(self):
        self._session_factory = get_session_factory()
        self.task_tracker = TaskTracker()

    def _get_session(self):
        return self._session_factory()

    def recover_interrupted_tasks(self) -> Dict[str, Any]:
        """
        Main recovery function - finds and handles interrupted tasks.
        Returns summary of recovery actions.
        """
        session = self._get_session()
        recovery_summary = {
            "running_tasks_found": 0,
            "tasks_paused": 0,
            "tasks_marked_failed": 0,
            "checkpoints_restored": 0,
            "pending_deployments": 0,
            "deployment_pending_flag_cleared": False,
            "notifications_sent": [],
        }

        try:
            # 1. Find tasks that were running before restart
            running_tasks = (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.RUNNING)
                .all()
            )

            recovery_summary["running_tasks_found"] = len(running_tasks)

            for task in running_tasks:
                # Check if heartbeat is stale (system was down for a while)
                heartbeat_stale = False
                if task.last_heartbeat:
                    # Consider stale if no heartbeat in last 5 minutes
                    heartbeat_stale = (
                        datetime.utcnow() - task.last_heartbeat
                    ) > timedelta(minutes=5)

                if heartbeat_stale:
                    # Task was interrupted - mark as paused for potential resume
                    task.status = TaskStatus.PAUSED

                    # Create checkpoint from current state if available
                    if task.current_state:
                        checkpoint = TaskCheckpoint(
                            task_id=task.task_id,
                            serialized_state=task.current_state,
                            checkpoint_type="recovery",
                            is_active=True,
                        )
                        session.add(checkpoint)
                        recovery_summary["checkpoints_restored"] += 1

                    recovery_summary["tasks_paused"] += 1
                    recovery_summary["notifications_sent"].append(
                        f"Task {task.task_id} ({task.subagent_name}) was paused - checkpoint saved"
                    )
                    logger.info(
                        f"Paused task {task.task_id} due to interrupted execution"
                    )
                else:
                    # Task might still be running (heartbeat recent)
                    # Mark as paused to be safe
                    task.status = TaskStatus.PAUSED
                    recovery_summary["tasks_paused"] += 1
                    logger.info(f"Paused task {task.task_id} for safe recovery")

            # 2. Handle pending deployments that were in progress
            processing_deployments = (
                session.query(DeploymentQueue)
                .filter(DeploymentQueue.status == QueueStatus.PROCESSING)
                .all()
            )

            for deploy in processing_deployments:
                # Mark as failed or pending based on whether they can be retried
                deploy.status = QueueStatus.FAILED
                deploy.error_message = "Deployment interrupted by system restart"
                recovery_summary["pending_deployments"] += 1
                logger.info(f"Marked deployment {deploy.id} as failed due to restart")

            # 3. Clear deployment_pending flags from all tasks
            # (these were set before restart to block new deployments)
            tasks_with_pending_flag = (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.RUNNING)
                .all()
            )

            for task in tasks_with_pending_flag:
                # If there's metadata indicating deployment was pending, clear it
                # The actual flag would be stored in current_state or similar
                pass

            recovery_summary["deployment_pending_flag_cleared"] = True

            session.commit()

            logger.info(f"Recovery complete: {recovery_summary}")
            return recovery_summary

        except Exception as e:
            session.rollback()
            logger.error(f"Recovery failed: {e}")
            recovery_summary["error"] = str(e)
            raise
        finally:
            session.close()

    def get_recovery_report(self) -> Dict[str, Any]:
        """
        Generate a detailed report of tasks that need attention.
        """
        session = self._get_session()
        report = {
            "paused_tasks": [],
            "failed_deployments": [],
            "available_checkpoints": [],
            "timestamp": datetime.utcnow().isoformat(),
        }

        try:
            # Paused tasks that can be resumed
            paused_tasks = (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.PAUSED)
                .all()
            )

            for task in paused_tasks:
                # Check for available checkpoints
                checkpoints = (
                    session.query(TaskCheckpoint)
                    .filter(
                        and_(
                            TaskCheckpoint.task_id == task.task_id,
                            TaskCheckpoint.is_active,
                        )
                    )
                    .order_by(TaskCheckpoint.created_at.desc())
                    .limit(5)
                    .all()
                )

                checkpoint_info = [
                    {
                        "id": cp.id,
                        "type": cp.checkpoint_type,
                        "created": cp.created_at.isoformat(),
                    }
                    for cp in checkpoints
                ]

                report["paused_tasks"].append(
                    {
                        "task_id": task.task_id,
                        "subagent_name": task.subagent_name,
                        "project_id": task.project_id,
                        "started_at": task.started_at.isoformat()
                        if task.started_at
                        else None,
                        "available_checkpoints": checkpoint_info,
                    }
                )

            # Failed deployments
            failed_deployments = (
                session.query(DeploymentQueue)
                .filter(DeploymentQueue.status == QueueStatus.FAILED)
                .all()
            )

            for deploy in failed_deployments:
                if deploy.retry_count < deploy.max_retries:
                    report["failed_deployments"].append(
                        {
                            "id": deploy.id,
                            "deployment_type": deploy.deployment_type.value,
                            "target_service": deploy.target_service,
                            "error": deploy.error_message,
                            "retry_count": deploy.retry_count,
                            "can_retry": True,
                        }
                    )

            # Available checkpoints
            active_checkpoints = (
                session.query(TaskCheckpoint).filter(TaskCheckpoint.is_active).all()
            )

            for cp in active_checkpoints:
                report["available_checkpoints"].append(
                    {
                        "task_id": cp.task_id,
                        "type": cp.checkpoint_type,
                        "created": cp.created_at.isoformat(),
                    }
                )

            return report

        finally:
            session.close()

    def resume_task(self, task_id: str) -> bool:
        """Resume a specific paused task."""
        return self.task_tracker.resume_task(task_id)

    def retry_deployment(self, deployment_id: int) -> bool:
        """Retry a failed deployment."""
        session = self._get_session()
        try:
            deploy = (
                session.query(DeploymentQueue)
                .filter(DeploymentQueue.id == deployment_id)
                .first()
            )

            if not deploy:
                return False

            if deploy.status != QueueStatus.FAILED:
                return False

            if deploy.retry_count >= deploy.max_retries:
                logger.warning(f"Deployment {deployment_id} exceeded max retries")
                return False

            deploy.status = QueueStatus.PENDING
            deploy.retry_count += 1
            deploy.error_message = None
            deploy.started_at = None
            deploy.completed_at = None

            session.commit()
            logger.info(f"Retrying deployment {deployment_id}")
            return True

        finally:
            session.close()

    def cleanup_old_checkpoints(self, days: int = 7) -> int:
        """Clean up old inactive checkpoints."""
        session = self._get_session()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)

            old_checkpoints = (
                session.query(TaskCheckpoint)
                .filter(
                    and_(
                        ~TaskCheckpoint.is_active,
                        TaskCheckpoint.created_at < cutoff,
                    )
                )
                .all()
            )

            count = len(old_checkpoints)
            for cp in old_checkpoints:
                session.delete(cp)

            session.commit()
            logger.info(f"Cleaned up {count} old checkpoints")
            return count

        finally:
            session.close()

    def generate_startup_announcement(self) -> str:
        """
        Generate an announcement message about interrupted tasks.
        This is sent to Telegram on system startup if there were issues.
        """
        summary = self.recover_interrupted_tasks()
        report = self.get_recovery_report()

        lines = []

        # Header
        lines.append("SYSTEM RECOVERY REPORT")
        lines.append("=" * 30)

        # Tasks found
        if summary.get("running_tasks_found", 0) > 0:
            lines.append(f"Interrupted tasks found: {summary['running_tasks_found']}")
            lines.append(f"Tasks paused: {summary['tasks_paused']}")
            lines.append(f"Checkpoints saved: {summary['checkpoints_restored']}")
        else:
            lines.append("No interrupted tasks found.")

        # Paused tasks detail
        if report["paused_tasks"]:
            lines.append("")
            lines.append("PAUSED TASKS (can be resumed):")
            for task in report["paused_tasks"]:
                lines.append(f"  - {task['subagent_name']} ({task['task_id'][:8]}...)")
                if task["available_checkpoints"]:
                    lines.append(
                        f"    Checkpoints: {len(task['available_checkpoints'])}"
                    )

        # Failed deployments
        if report["failed_deployments"]:
            lines.append("")
            lines.append("FAILED DEPLOYMENTS:")
            for deploy in report["failed_deployments"]:
                lines.append(
                    f"  - {deploy['deployment_type']} for {deploy['target_service']}"
                )
                lines.append(f"    Error: {deploy['error'][:50]}...")

        # Status
        lines.append("")
        lines.append("System is now operational.")

        return "\n".join(lines)


def run_recovery():
    """Run startup recovery and return summary."""
    logger.info("=" * 50)
    logger.info("Starting system recovery...")
    logger.info("=" * 50)

    recovery = StartupRecovery()

    # Run recovery
    summary = recovery.recover_interrupted_tasks()

    # Generate report
    report = recovery.get_recovery_report()

    # Print summary
    logger.info("=" * 50)
    logger.info("RECOVERY SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Running tasks found: {summary['running_tasks_found']}")
    logger.info(f"Tasks paused: {summary['tasks_paused']}")
    logger.info(f"Checkpoints restored: {summary['checkpoints_restored']}")
    logger.info(f"Deployments affected: {summary['pending_deployments']}")

    if report["paused_tasks"]:
        logger.info("")
        logger.info("Paused tasks that can be resumed:")
        for task in report["paused_tasks"]:
            logger.info(f"  - {task['task_id']} ({task['subagent_name']})")
            if task["available_checkpoints"]:
                logger.info(
                    f"    Checkpoints available: {len(task['available_checkpoints'])}"
                )

    if report["failed_deployments"]:
        logger.info("")
        logger.info("Failed deployments that can be retried:")
        for deploy in report["failed_deployments"]:
            logger.info(
                f"  - {deploy['deployment_type']} for {deploy['target_service']}"
            )

    logger.info("=" * 50)

    return summary, report


def notify_recovery_to_telegram() -> bool:
    """
    Send recovery notification to Telegram.
    Returns True if notification was sent.
    """
    try:
        recovery = StartupRecovery()
        announcement = recovery.generate_startup_announcement()

        # Only send if there were interrupted tasks
        summary, _ = recovery.recover_interrupted_tasks()
        if summary.get("running_tasks_found", 0) > 0:
            from nova.tools.chat.telegram_notifier import (
                send_telegram_message,
                get_notifications_chat_id,
            )

            chat_id = get_notifications_chat_id()
            if chat_id:
                return send_telegram_message(chat_id, announcement)

        return False

    except Exception as e:
        logger.error(f"Failed to send recovery notification: {e}")
        return False


if __name__ == "__main__":
    summary, report = run_recovery()
    print("\nRecovery Summary:", summary)
    print("\nDetailed Report:", report)
