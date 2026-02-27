"""
Deployment Coordinator - Orchestrates safe deployments

This module coordinates deployments with worker awareness,
handles state persistence, and manages scheduled jobs with auto-resume.
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from croniter import croniter

from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError

from nova.db.engine import get_session_factory
from nova.db.deployment_models import (
    DeploymentQueue, DeploymentType, QueuePriority, QueueStatus,
    ActiveTask, TaskStatus, ScheduledJob
)
from nova.queue_manager import QueueManager
from nova.task_tracker import TaskTracker

logger = logging.getLogger(__name__)


class DeploymentCoordinator:
    """
    Orchestrates deployment operations with worker coordination.
    Handles queue processing, state persistence, and scheduled jobs.
    """
    
    def __init__(self):
        self._session_factory = get_session_factory()
        self._lock = threading.RLock()
        self._running = False
        self._process_thread: Optional[threading.Thread] = None
        self._scheduler_thread: Optional[threading.Thread] = None
        
        # Core components
        self.queue_manager = QueueManager()
        self.task_tracker = TaskTracker()
        
        # Connect queue manager to task tracker
        self.queue_manager.set_worker_check_callback(self.task_tracker.get_active_count)
        
        # Execution callbacks
        self._deployment_executor: Optional[Callable] = None
        self._notification_callback: Optional[Callable] = None
        
        # Settings
        self._poll_interval = 5  # seconds
        self._scheduler_poll_interval = 60  # seconds
        
    def _get_session(self):
        return self._session_factory()
    
    def set_deployment_executor(self, executor: Callable[[DeploymentQueue], bool]):
        """Set the function that executes actual deployments."""
        self._deployment_executor = executor
        
    def set_notification_callback(self, callback: Callable[[str, str], None]):
        """Set callback for sending notifications."""
        self._notification_callback = callback
        self.queue_manager.set_notification_callback(callback)
    
    def start(self):
        """Start the deployment coordinator."""
        with self._lock:
            if self._running:
                logger.warning("DeploymentCoordinator already running")
                return
            
            self._running = True
            
            # Start queue processor thread
            self._process_thread = threading.Thread(
                target=self._process_queue_loop,
                name="DeploymentProcessor",
                daemon=True
            )
            self._process_thread.start()
            
            # Start scheduler thread
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name="JobScheduler",
                daemon=True
            )
            self._scheduler_thread.start()
            
            logger.info("DeploymentCoordinator started")
    
    def stop(self):
        """Stop the deployment coordinator."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            
            if self._process_thread:
                self._process_thread.join(timeout=10)
            if self._scheduler_thread:
                self._scheduler_thread.join(timeout=10)
            
            logger.info("DeploymentCoordinator stopped")
    
    def _process_queue_loop(self):
        """Main loop for processing deployment queue."""
        logger.info("Queue processor loop started")
        
        while self._running:
            try:
                self._process_next_deployment()
            except Exception as e:
                logger.error(f"Error in queue processing: {e}")
            
            time.sleep(self._poll_interval)
    
    def _process_next_deployment(self):
        """Process the next deployment in queue."""
        # Get next pending item
        queue_item = self.queue_manager.get_next_pending()
        if not queue_item:
            return
        
        # Check if can proceed (worker coordination)
        can_proceed, message = self.queue_manager.check_can_proceed(queue_item.id)
        
        if not can_proceed:
            logger.info(f"Queue item {queue_item.id}: {message}")
            return
        
        # Mark as processing
        self.queue_manager.update_status(queue_item.id, QueueStatus.PROCESSING)
        
        # Notify start
        if self._notification_callback and queue_item.requested_by:
            self._notification_callback(
                queue_item.requested_by,
                f"Starting deployment: {queue_item.deployment_type.value} "
                f"for {queue_item.target_service}"
            )
        
        # Pause active tasks if this is a destructive action
        if queue_item.requires_state_pause:
            paused_count = self.task_tracker.pause_all_active()
            logger.info(f"Paused {paused_count} active tasks for deployment")
        
        # Execute deployment
        success = False
        error_msg = None
        
        if self._deployment_executor:
            try:
                success = self._deployment_executor(queue_item)
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Deployment execution failed: {e}")
        else:
            error_msg = "No deployment executor configured"
            logger.warning(error_msg)
        
        # Update status
        if success:
            self.queue_manager.update_status(queue_item.id, QueueStatus.COMPLETED)
            
            # Resume paused tasks after successful deployment
            self._resume_paused_tasks()
            
            if self._notification_callback and queue_item.requested_by:
                self._notification_callback(
                    queue_item.requested_by,
                    f"Deployment completed: {queue_item.deployment_type.value} "
                    f"for {queue_item.target_service}"
                )
        else:
            self.queue_manager.update_status(
                queue_item.id,
                QueueStatus.FAILED,
                error_message=error_msg
            )
            
            # Resume tasks even on failure
            self._resume_paused_tasks()
            
            if self._notification_callback and queue_item.requested_by:
                self._notification_callback(
                    queue_item.requested_by,
                    f"Deployment failed: {queue_item.deployment_type.value} "
                    f"for {queue_item.target_service}. Error: {error_msg}"
                )
    
    def _resume_paused_tasks(self):
        """Resume all paused tasks after deployment completes."""
        session = self._get_session()
        try:
            paused_tasks = session.query(ActiveTask).filter(
                ActiveTask.status == TaskStatus.PAUSED
            ).all()
            
            for task in paused_tasks:
                task.status = TaskStatus.RUNNING
                # Note: state will be restored from checkpoint when task checks in
            
            session.commit()
            logger.info(f"Resumed {len(paused_tasks)} paused tasks")
            
        finally:
            session.close()
    
    def _scheduler_loop(self):
        """Main loop for scheduled job management."""
        logger.info("Scheduler loop started")
        
        while self._running:
            try:
                self._process_scheduled_jobs()
            except Exception as e:
                logger.error(f"Error in scheduler: {e}")
            
            time.sleep(self._scheduler_poll_interval)
    
    def _process_scheduled_jobs(self):
        """Process due scheduled jobs."""
        session = self._get_session()
        try:
            now = datetime.utcnow()
            
            # Find jobs that are due and not currently running
            due_jobs = session.query(ScheduledJob).filter(
                and_(
                    ScheduledJob.is_enabled == True,
                    ScheduledJob.is_running == False,
                    or_(
                        ScheduledJob.next_run.is_(None),
                        ScheduledJob.next_run <= now
                    )
                )
            ).all()
            
            for job in due_jobs:
                self._execute_scheduled_job(job, session)
            
        finally:
            session.close()
    
    def _execute_scheduled_job(self, job, session):
        """Execute a scheduled job."""
        logger.info(f"Executing scheduled job: {job.job_id}")
        
        # Mark as running
        job.is_running = True
        job.last_run = datetime.utcnow()
        
        # Check for checkpoint to resume from
        checkpoint = None
        if job.auto_resume and job.last_checkpoint_id:
            from nova.db.deployment_models import TaskCheckpoint
            checkpoint = session.query(TaskCheckpoint).filter(
                TaskCheckpoint.id == job.last_checkpoint_id
            ).first()
        
        session.commit()
        
        # Execute the job (placeholder - would call actual job function)
        success = True  # Would be result of actual execution
        
        # Update job status
        job.is_running = False
        job.last_status = "success" if success else "failed"
        
        # Calculate next run
        if job.cron_expression:
            try:
                cron = croniter(job.cron_expression, job.last_run)
                job.next_run = cron.get_next(datetime)
            except Exception as e:
                logger.error(f"Invalid cron expression for job {job.job_id}: {e}")
        
        # Save checkpoint if enabled
        if job.auto_resume and success:
            from nova.db.deployment_models import TaskCheckpoint
            # Would serialize job state here
            # checkpoint = TaskCheckpoint(...)
            # session.add(checkpoint)
            # job.last_checkpoint_id = checkpoint.id
            pass
        
        session.commit()
    
    # ==================== Public API ====================
    
    def queue_deployment(
        self,
        deployment_type: str,
        target_service: str,
        requested_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        """
        Add a deployment to the queue.
        """
        try:
            dep_type = DeploymentType(deployment_type)
        except ValueError:
            raise ValueError(f"Invalid deployment type: {deployment_type}")
        
        return self.queue_manager.add_to_queue(
            deployment_type=dep_type,
            target_service=target_service,
            requested_by=requested_by,
            reason=reason,
        )
    
    def get_queue_status(self) -> List[Dict[str, Any]]:
        """Get current queue status."""
        return self.queue_manager.get_queue_status()
    
    def get_active_tasks(self) -> List[Dict[str, Any]]:
        """Get all active tasks."""
        return self.task_tracker.get_active_tasks()
    
    def cancel_deployment(self, queue_id: int) -> bool:
        """Cancel a pending deployment."""
        return self.queue_manager.cancel_queue_item(queue_id)
    
    def register_scheduled_job(
        self,
        job_id: str,
        job_name: str,
        cron_expression: str,
        auto_resume: bool = True,
    ) -> bool:
        """Register a new scheduled job."""
        session = self._get_session()
        try:
            # Check if exists
            existing = session.query(ScheduledJob).filter(
                ScheduledJob.job_id == job_id
            ).first()
            
            if existing:
                logger.warning(f"Job {job_id} already registered")
                return False
            
            job = ScheduledJob(
                job_id=job_id,
                job_name=job_name,
                cron_expression=cron_expression,
                auto_resume=auto_resume,
                is_enabled=True,
            )
            
            # Calculate first run
            if cron_expression:
                try:
                    cron = croniter(cron_expression, datetime.utcnow())
                    job.next_run = cron.get_next(datetime)
                except Exception as e:
                    logger.error(f"Invalid cron expression: {e}")
            
            session.add(job)
            session.commit()
            
            logger.info(f"Registered scheduled job: {job_id}")
            return True
            
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to register job: {e}")
            return False
        finally:
            session.close()
    
    def toggle_scheduled_job(self, job_id: str, enabled: bool) -> bool:
        """Enable or disable a scheduled job."""
        session = self._get_session()
        try:
            job = session.query(ScheduledJob).filter(
                ScheduledJob.job_id == job_id
            ).first()
            
            if not job:
                return False
            
            job.is_enabled = enabled
            session.commit()
            return True
            
        finally:
            session.close()
    
    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        """Get all scheduled jobs."""
        session = self._get_session()
        try:
            jobs = session.query(ScheduledJob).all()
            
            return [
                {
                    "job_id": j.job_id,
                    "job_name": j.job_name,
                    "cron_expression": j.cron_expression,
                    "is_enabled": j.is_enabled,
                    "is_running": j.is_running,
                    "last_run": j.last_run.isoformat() if j.last_run else None,
                    "next_run": j.next_run.isoformat() if j.next_run else None,
                    "last_status": j.last_status,
                    "auto_resume": j.auto_resume,
                }
                for j in jobs
            ]
        finally:
            session.close()