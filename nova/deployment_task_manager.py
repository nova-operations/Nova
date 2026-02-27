"""
Deployment and Task Manager Integration Module

This module provides a unified interface for managing deployments,
task persistence, and startup recovery. It's designed to be imported
on system startup to handle recovery and coordinate deployments.
"""

import logging
import os
import sys
from typing import Optional, Dict, Any, List

# Ensure project root is in path
sys.path.insert(0, os.getcwd())

from nova.db.engine import get_session_factory
from nova.queue_manager import QueueManager
from nova.task_tracker import TaskTracker
from nova.deployment_coordinator import DeploymentCoordinator
from nova.startup_recovery import StartupRecovery

logger = logging.getLogger(__name__)


class DeploymentTaskManager:
    """
    Unified manager for deployment queuing and task persistence.

    This class provides a single interface for:
    - Starting up and recovering from previous state
    - Registering and tracking subagent tasks
    - Managing deployment queue with concurrency safety
    - Checking if deployments can proceed
    """

    _instance: Optional["DeploymentTaskManager"] = None

    def __new__(cls):
        """Singleton pattern for consistent state."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._session_factory = get_session_factory()

        # Core components
        self.queue_manager = QueueManager()
        self.task_tracker = TaskTracker()
        self.coordinator = DeploymentCoordinator()

        # Connect components
        self.queue_manager.set_worker_check_callback(self.task_tracker.get_active_count)

        logger.info("DeploymentTaskManager initialized")

    def initialize_on_startup(self, run_recovery: bool = True) -> Dict[str, Any]:
        """
        Initialize the system on startup.
        Runs recovery if specified, returns status.
        Also seeds default specialist configurations.
        """
        result = {
            "initialized": True,
            "recovery_performed": False,
            "recovery_summary": None,
            "specialists_seeded": False,
            "seeding_result": None,
        }

        # Seed default specialists first (needed for team tasks)
        try:
            from nova.tools.specialist_registry import seed_default_specialists
            seeding_result = seed_default_specialists()
            result["specialists_seeded"] = True
            result["seeding_result"] = seeding_result
            logger.info(f"Specialist seeding: {seeding_result}")
        except Exception as e:
            result["seeding_result"] = f"Error: {e}"
            logger.error(f"Failed to seed specialists: {e}")

        if run_recovery:
            try:
                recovery = StartupRecovery()
                # recover_interrupted_tasks returns a dict (summary)
                summary = recovery.recover_interrupted_tasks()
                # Get report separately
                report = recovery.get_recovery_report()

                result["recovery_performed"] = True
                result["recovery_summary"] = summary
                result["recovery_report"] = report
                logger.info(f"Startup recovery complete: {summary}")
            except Exception as e:
                logger.error(f"Startup recovery failed: {e}")
                result["recovery_error"] = str(e)

        return result

    def register_subagent_task(
        self,
        task_id: str,
        task_type: str,
        subagent_name: str,
        project_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """
        Register a new subagent task.
        Returns True if registered successfully.
        """
        return self.task_tracker.register_task(
            task_id=task_id,
            task_type=task_type,
            subagent_name=subagent_name,
            project_id=project_id,
            description=description,
        )

    def unregister_subagent_task(self, task_id: str) -> bool:
        """Unregister a completed task."""
        return self.task_tracker.unregister_task(task_id)

    def get_active_tasks(
        self,
        project_id: Optional[str] = None,
        subagent_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all active tasks, optionally filtered."""
        return self.task_tracker.get_active_tasks(
            project_id=project_id,
            subagent_name=subagent_name,
        )

    def get_task_count(self) -> int:
        """Get count of active tasks."""
        return self.task_tracker.get_active_count()

    def can_deploy(self, check_project: Optional[str] = None) -> tuple[bool, str]:
        """
        Check if a deployment can proceed.
        Returns (can_deploy, reason).
        """
        # Check if there are active tasks
        active_count = self.get_task_count()

        if active_count > 0:
            # Check if deployment_pending flag is set on any task
            # (this would be set during critical sections)
            tasks = self.task_tracker.get_active_tasks()
            for task in tasks:
                state = self.task_tracker.get_task_state(task["task_id"])
                if state and state.get("deployment_pending"):
                    return (
                        False,
                        f"Task {task['task_id']} has deployment_pending flag set",
                    )

            return False, f"{active_count} task(s) still running"

        return True, "No active tasks - deployment can proceed"

    def add_to_deployment_queue(
        self,
        deployment_type: str,
        target_service: str,
        requested_by: Optional[str] = None,
        reason: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> int:
        """
        Add a deployment to the queue.
        """
        from nova.db.deployment_models import DeploymentType, QueuePriority

        try:
            dep_type = DeploymentType(deployment_type)
        except ValueError:
            raise ValueError(f"Invalid deployment type: {deployment_type}")

        prio = None
        if priority:
            try:
                prio = QueuePriority[priority.upper()]
            except KeyError:
                pass

        return self.queue_manager.add_to_queue(
            deployment_type=dep_type,
            target_service=target_service,
            requested_by=requested_by,
            reason=reason,
            priority=prio,
        )

    def get_queue_status(self) -> List[Dict[str, Any]]:
        """Get current queue status."""
        return self.queue_manager.get_queue_status()

    def create_task_checkpoint(
        self,
        task_id: str,
        state: Dict[str, Any],
        checkpoint_type: str = "manual",
    ) -> Optional[int]:
        """Create a checkpoint for task state."""
        return self.task_tracker.create_checkpoint(
            task_id=task_id,
            state=state,
            checkpoint_type=checkpoint_type,
        )

    def get_task_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get latest checkpoint for a task."""
        return self.task_tracker.get_latest_checkpoint(task_id)

    def start_coordinator(self):
        """Start the deployment coordinator background threads."""
        self.coordinator.start()
        logger.info("Deployment coordinator started")

    def stop_coordinator(self):
        """Stop the deployment coordinator."""
        self.coordinator.stop()
        logger.info("Deployment coordinator stopped")


# Global singleton instance
_manager: Optional[DeploymentTaskManager] = None


def get_manager() -> DeploymentTaskManager:
    """Get the global DeploymentTaskManager instance."""
    global _manager
    if _manager is None:
        _manager = DeploymentTaskManager()
    return _manager


def initialize_system(run_recovery: bool = True) -> Dict[str, Any]:
    """
    Convenience function to initialize the system on startup.
    Call this from your main application startup code.
    """
    manager = get_manager()
    return manager.initialize_on_startup(run_recovery)


# Import tuple for type hint
from typing import Tuple