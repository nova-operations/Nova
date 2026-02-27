"""
Deployment Services - Integration layer for the deployment system

This module provides a unified API for the deployment queue system,
integrating with the existing bot and agent infrastructure.
"""

import logging
from typing import Optional, Dict, Any, List, Callable

from nova.deployment_coordinator import DeploymentCoordinator
from nova.task_tracker import TaskTracker
from nova.queue_manager import QueueManager
from nova.db.deployment_models import (
    DeploymentType, QueuePriority, QueueStatus, TaskStatus
)

logger = logging.getLogger(__name__)


class DeploymentService:
    """
    Unified service interface for deployment management.
    Integrates queue, task tracking, and coordination.
    """
    
    _instance: Optional['DeploymentService'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._coordinator = DeploymentCoordinator()
        self._task_tracker = TaskTracker()
        self._queue_manager = QueueManager()
        
        # Default notification handler (can be overridden)
        self._notification_handler: Optional[Callable] = None
        
        logger.info("DeploymentService initialized")
    
    def initialize(
        self,
        deployment_executor: Callable,
        notification_handler: Optional[Callable] = None,
    ):
        """
        Initialize the service with required callbacks.
        
        Args:
            deployment_executor: Function to execute actual deployments
            notification_handler: Function to send notifications to users
        """
        self._coordinator.set_deployment_executor(deployment_executor)
        
        if notification_handler:
            self._notification_handler = notification_handler
            self._coordinator.set_notification_callback(notification_handler)
        
        # Connect task tracker to queue manager
        self._coordinator.queue_manager.set_worker_check_callback(
            self._task_tracker.get_active_count
        )
        
        # Initialize database tables
        from nova.db.init_deployment import init_deployment_db
        init_deployment_db()
        
        logger.info("DeploymentService ready")
    
    def start(self):
        """Start the deployment coordinator."""
        self._coordinator.start()
    
    def stop(self):
        """Stop the deployment coordinator."""
        self._coordinator.stop()
    
    # ==================== Task Management ====================
    
    def register_task(
        self,
        task_id: str,
        task_type: str,
        subagent_name: str,
        project_id: Optional[str] = None,
        description: Optional[str] = None,
        initial_state: Optional[Dict] = None,
    ) -> bool:
        """Register a new task with the tracker."""
        return self._task_tracker.register_task(
            task_id=task_id,
            task_type=task_type,
            subagent_name=subagent_name,
            project_id=project_id,
            description=description,
            initial_state=initial_state,
        )
    
    def complete_task(
        self,
        task_id: str,
        final_state: Optional[Dict] = None,
    ) -> bool:
        """Mark a task as completed."""
        return self._task_tracker.unregister_task(task_id, final_state)
    
    def update_task_heartbeat(self, task_id: str) -> bool:
        """Update task heartbeat."""
        return self._task_tracker.update_heartbeat(task_id)
    
    def update_task_progress(self, task_id: str, progress: int) -> bool:
        """Update task progress."""
        return self._task_tracker.update_progress(task_id, progress)
    
    def update_task_state(self, task_id: str, state: Dict) -> bool:
        """Update task state."""
        return self._task_tracker.update_state(task_id, state)
    
    def get_task_state(self, task_id: str) -> Optional[Dict]:
        """Get task state."""
        return self._task_tracker.get_task_state(task_id)
    
    def create_task_checkpoint(
        self,
        task_id: str,
        state: Dict,
        checkpoint_type: str = "manual",
    ) -> Optional[int]:
        """Create a checkpoint for a task."""
        return self._task_tracker.create_checkpoint(task_id, state, checkpoint_type)
    
    def get_active_tasks(
        self,
        project_id: Optional[str] = None,
        subagent_name: Optional[str] = None,
    ) -> List[Dict]:
        """Get all active tasks."""
        return self._task_tracker.get_active_tasks(project_id, subagent_name)
    
    def get_active_task_count(self) -> int:
        """Get count of active tasks."""
        return self._task_tracker.get_active_count()
    
    # ==================== Deployment Queue ====================
    
    def queue_deployment(
        self,
        deployment_type: str,
        target_service: str,
        requested_by: Optional[str] = None,
        reason: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> int:
        """
        Queue a deployment for execution.
        
        Args:
            deployment_type: Type of deployment (deploy, redeploy, restart, etc.)
            target_service: Target service or project name
            requested_by: User who requested the deployment
            reason: Reason for deployment
            priority: Priority level (low, normal, high, critical)
        
        Returns:
            Queue item ID
        """
        prio = None
        if priority:
            try:
                prio = QueuePriority[priority.upper()]
            except KeyError:
                logger.warning(f"Invalid priority: {priority}, using default")
        
        return self._coordinator.queue_deployment(
            deployment_type=deployment_type,
            target_service=target_service,
            requested_by=requested_by,
            reason=reason,
        )
    
    def cancel_deployment(self, queue_id: int) -> bool:
        """Cancel a pending deployment."""
        return self._coordinator.cancel_deployment(queue_id)
    
    def get_queue_status(self) -> List[Dict]:
        """Get current deployment queue status."""
        return self._coordinator.get_queue_status()
    
    # ==================== Scheduled Jobs ====================
    
    def register_scheduled_job(
        self,
        job_id: str,
        job_name: str,
        cron_expression: str,
        auto_resume: bool = True,
    ) -> bool:
        """Register a scheduled job."""
        return self._coordinator.register_scheduled_job(
            job_id=job_id,
            job_name=job_name,
            cron_expression=cron_expression,
            auto_resume=auto_resume,
        )
    
    def toggle_scheduled_job(self, job_id: str, enabled: bool) -> bool:
        """Enable or disable a scheduled job."""
        return self._coordinator.toggle_scheduled_job(job_id, enabled)
    
    def get_scheduled_jobs(self) -> List[Dict]:
        """Get all scheduled jobs."""
        return self._coordinator.get_scheduled_jobs()
    
    # ==================== Utility ====================
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status."""
        return {
            "active_tasks": self.get_active_task_count(),
            "queue_items": len(self.get_queue_status()),
            "scheduled_jobs": len(self.get_scheduled_jobs()),
            "queue": self.get_queue_status()[:5],  # First 5 items
        }


# Singleton instance
deployment_service = DeploymentService()