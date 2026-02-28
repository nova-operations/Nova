"""
Deployment Queue Manager - Core Models

This module defines the database models for the concurrency-safe
queuing and deployment management system.
"""

import enum
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    Enum,
    Boolean,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from nova.db.base import Base


class DeploymentType(enum.Enum):
    """Types of deployment actions."""

    DEPLOY = "deploy"
    REDEPLOY = "redeploy"
    RESTART = "restart"
    SCALE = "scale"
    ROLLBACK = "rollback"


class QueuePriority(enum.Enum):
    """Priority levels for deployment queue."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class QueueStatus(enum.Enum):
    """Status of queued deployment."""

    PENDING = "pending"
    WAITING_FOR_WORKERS = "waiting_for_workers"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, enum.Enum):
    """Task type enumeration."""

    STANDALONE_SH = "standalone_sh"
    SUBAGENT_RECALL = "subagent_recall"
    TEAM_TASK = "team_task"
    SILENT = "silent"
    ALERT = "alert"
    WATCHER = "watcher"


class TaskStatus(enum.Enum):
    """Status of active tasks."""

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectContext(Base):
    """
    Table for tracking multiple projects/environments managed by Nova.
    """

    __tablename__ = "project_contexts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    absolute_path = Column(String(1024), nullable=False)
    git_remote = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=False)
    metadata_json = Column(Text, nullable=True)  # flexible JSON storage for state

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DeploymentQueue(Base):
    """
    Table for tracking deployment queue items.
    High-priority items (Redeploy/Restart) are processed first.
    """

    __tablename__ = "deployment_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Deployment details
    deployment_type = Column(Enum(DeploymentType), nullable=False)
    target_service = Column(String(255), nullable=False)
    priority = Column(Enum(QueuePriority), default=QueuePriority.NORMAL)
    status = Column(Enum(QueueStatus), default=QueueStatus.PENDING)

    # Metadata
    requested_by = Column(String(255), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    scheduled_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # State persistence
    requires_state_pause = Column(Boolean, default=False)

    # Error tracking
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)


class ActiveTask(Base):
    """
    Table for tracking currently active subagent tasks.
    Used to prevent state collisions and coordinate deployments.
    """

    __tablename__ = "active_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Task identification
    task_id = Column(String(255), unique=True, nullable=False, index=True)
    task_type = Column(String(100), nullable=False)
    subagent_name = Column(String(100), nullable=False)

    # Status tracking
    status = Column(Enum(TaskStatus), default=TaskStatus.RUNNING)
    started_at = Column(DateTime, default=datetime.utcnow)
    last_heartbeat = Column(DateTime, default=datetime.utcnow)

    # State for resume capability
    current_state = Column(Text, nullable=True)  # JSON serialized state
    progress_percentage = Column(Integer, default=0)

    # Metadata
    project_id = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=True)


class TaskCheckpoint(Base):
    """
    Table for persisting task state for resume after restart.
    """

    __tablename__ = "task_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Link to task
    task_id = Column(String(255), nullable=False, index=True)

    # Link to deployment (optional - for pre-deployment checkpoints)
    deployment_queue_id = Column(
        Integer, ForeignKey("deployment_queue.id"), nullable=True
    )

    # State data (JSON serialized)
    serialized_state = Column(Text, nullable=False)

    # Metadata
    checkpoint_type = Column(String(50), default="manual")  # manual, auto, pre_deploy
    created_at = Column(DateTime, default=datetime.utcnow)

    # For cleanup tracking
    is_active = Column(Boolean, default=True)

    # Relationships
    deployment = relationship("DeploymentQueue", backref="checkpoints")


class ScheduledJob(Base):
    """
    Table for scheduled cron jobs that need auto-resume capability.
    """

    __tablename__ = "scheduled_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Job identification
    job_id = Column(String(255), unique=True, nullable=False, index=True)
    job_name = Column(String(255), nullable=False)
    cron_expression = Column(String(100), nullable=False)

    # Status
    is_enabled = Column(Boolean, default=True)
    is_running = Column(Boolean, default=False)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)  # success, failed, running

    # Resume capability
    last_checkpoint_id = Column(Integer, nullable=True)
    auto_resume = Column(Boolean, default=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NotificationLog(Base):
    """
    Table for tracking user notifications.
    """

    __tablename__ = "notification_log"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Recipient
    user_id = Column(String(100), nullable=False, index=True)
    chat_id = Column(String(100), nullable=True)

    # Message details
    message_type = Column(
        String(50), nullable=False
    )  # queue_added, deployment_started, etc.
    message = Column(Text, nullable=False)

    # Status
    is_sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
