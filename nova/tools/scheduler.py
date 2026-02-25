"""
Nova Scheduler - Persistent Cron Task System

Provides DB-backed scheduling for:
- Standalone shell scripts
- Subagent recalls
- Silent background tasks

Supports notification via Telegram and full CRUD operations.
"""

import os
import asyncio
import logging
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List
from croniter import croniter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, Enum, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv
import enum

load_dotenv()
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE SCHEMA
# ============================================================================

Base = declarative_base()


class TaskStatus(str, enum.Enum):
    """Task status enumeration."""
    ACTIVE = "active"
    PAUSED = "paused"


class TaskType(str, enum.Enum):
    """Task type enumeration."""
    STANDALONE_SH = "standalone_sh"
    SUBAGENT_RECALL = "subagent_recall"
    TEAM_TASK = "team_task"
    SILENT = "silent"


class ScheduledTask(Base):
    """Scheduled task database model."""
    __tablename__ = "scheduled_tasks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String(255), nullable=False, unique=True)
    schedule = Column(String(100), nullable=False)  # Cron format
    task_type = Column(Enum(TaskType), nullable=False)
    script_path = Column(Text, nullable=True)  # Path to shell script
    subagent_name = Column(String(255), nullable=True)  # Name for subagent
    subagent_instructions = Column(Text, nullable=True)  # System instructions for subagent
    subagent_task = Column(Text, nullable=True)  # Task prompt for subagent
    team_members = Column(JSON, nullable=True)  # List of specialist names for TEAM_TASK
    status = Column(Enum(TaskStatus), default=TaskStatus.ACTIVE)
    notification_enabled = Column(Boolean, default=True)
    last_run = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)  # success, failure, skipped
    last_output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# DATABASE CONNECTION
# ============================================================================

def get_db_engine():
    """Create SQLAlchemy engine from DATABASE_URL."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set in environment")
    
    # Convert postgres:// to postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    # Use asyncpg for PostgreSQL
    if database_url.startswith("postgresql://"):
        # psycopg2-compatible URL for SQLAlchemy jobstore
        engine = create_engine(database_url, pool_pre_ping=True, echo=False)
    else:
        raise ValueError(f"Unsupported database: {database_url}")
    
    return engine


def init_db():
    """Initialize the database tables and run migrations."""
    engine = get_db_engine()
    Base.metadata.create_all(engine)
    
    # Also ensure specialist registry table is created (sharing engine)
    try:
        from nova.tools.specialist_registry import Base as SpecialistBase
        SpecialistBase.metadata.create_all(engine)
    except Exception as e:
        logger.warning(f"Could not initialize specialist registry tables: {e}")
    
    # Manual migration for team_members column if it doesn't exist
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    
    # Safer check using inspector
    columns = [c['name'] for c in inspector.get_columns('scheduled_tasks')]
    if 'team_members' not in columns:
        logger.info("Adding team_members column to scheduled_tasks table...")
        with engine.begin() as conn:  # engine.begin() handles transaction start/commit
            try:
                # Use JSONB for Postgres if possible, fallback to JSON
                col_type = "JSONB" if "postgresql" in str(engine.url) else "JSON"
                conn.execute(text(f"ALTER TABLE scheduled_tasks ADD COLUMN team_members {col_type}"))
                logger.info("✅ Successfully added team_members column.")
            except Exception as e:
                logger.error(f"Failed to add team_members column: {e}")
    
    return engine


# ============================================================================
# SCHEDULER IMPLEMENTATION
# ============================================================================

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None
_job_stores: Dict[str, Any] = {}


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    
    if _scheduler is not None:
        return _scheduler
    
    # Create database engine for job store
    engine = get_db_engine()
    
    jobstores = {
        'default': SQLAlchemyJobStore(
            engine=engine,
            metadata=Base.metadata,
            tablename="apscheduler_jobs"
        )
    }
    
    _scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        job_defaults={
            'coalesce': True,  # Combine missed runs into one
            'max_instances': 1,  # Only one instance at a time
            'misfire_grace_time': 300  # 5 minutes grace period
        },
        timezone='UTC'
    )
    
    return _scheduler


def _get_telegram_bot():
    """Get telegram bot instance for notifications."""
    try:
        from telegram import Bot
        from telegram.error import TelegramError
        
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not token or not chat_id:
            logger.debug("Telegram credentials not configured")
            return None
        
        return Bot(token=token), chat_id
    except ImportError:
        logger.debug("Telegram not available")
        return None


async def _send_telegram_notification(message: str, chat_id: Optional[str] = None):
    """Send notification via Telegram."""
    tg = _get_telegram_bot()
    if not tg:
        return
    
    bot, default_chat_id = tg
    target_chat_id = chat_id or default_chat_id
    
    if not target_chat_id:
        logger.debug("No chat_id configured for notifications")
        return
    
    try:
        await bot.send_message(text=message, chat_id=target_chat_id)
    except Exception as e:
        logger.error(f"Failed to send telegram notification: {e}")


async def _execute_standalone_shell(job_id: int, script_path: str, notification_enabled: bool):
    """Execute a standalone shell script."""
    logger.info(f"Executing standalone script: {script_path}")
    
    try:
        # Run the shell script
        process = await asyncio.create_subprocess_shell(
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        output = stdout.decode('utf-8', errors='replace')
        error = stderr.decode('utf-8', errors='replace')
        
        if process.returncode != 0:
            output = f"Error (code {process.returncode}): {error}\n{output}"
            logger.error(f"Script failed: {output}")
            return "failure", output
        else:
            logger.info(f"Script completed successfully")
            return "success", output
    
    except Exception as e:
        logger.error(f"Failed to execute script: {e}")
        return "failure", str(e)


async def _execute_subagent_recall(
    job_id: int,
    subagent_name: str,
    subagent_instructions: str,
    subagent_task: str,
    notification_enabled: bool
):
    """Execute a subagent recall."""
    logger.info(f"Executing subagent recall: {subagent_name}")
    
    try:
        # Import here to avoid circular imports
        from nova.tools.subagent import create_subagent
        
        # Create subagent synchronously
        result = await create_subagent(
            name=subagent_name,
            instructions=subagent_instructions,
            task=subagent_task
        )
        
        if result.startswith("Error"):
            return "failure", result
        
        # For recall tasks, we don't wait for the subagent to complete
        # We just trigger it and return
        notification_msg = f"🚀 Subagent '{subagent_name}' triggered by scheduled task (ID: {job_id})"
        
        if notification_enabled:
            await _send_telegram_notification(notification_msg)
        
        return "success", f"Subagent triggered: {result}"
    
    except Exception as e:
        logger.error(f"Failed to execute subagent recall: {e}")
        return "failure", str(e)


async def _execute_team_task(
    job_id: int,
    task_name: str,
    specialist_names: List[str],
    task_description: str,
    notification_enabled: bool
):
    """Execute a team task recall."""
    logger.info(f"Executing scheduled team task: {task_name}")
    
    try:
        from nova.tools.team_manager import run_team_task
        
        result = await run_team_task(
            task_name=task_name,
            specialist_names=specialist_names,
            task_description=task_description
        )
        
        if result.startswith("❌"):
            return "failure", result
            
        if notification_enabled:
            notification_msg = f"👥 Team Task '{task_name}' ({len(specialist_names)} agents) triggered by schedule (ID: {job_id})"
            await _send_telegram_notification(notification_msg)
            
        return "success", result
    except Exception as e:
        logger.error(f"Failed to execute team task: {e}")
        return "failure", str(e)


async def _execute_silent_task(job_id: int):
    """Execute a silent task (no output/notification)."""
    logger.info(f"Executing silent task: {job_id}")
    return "success", "Silent task completed"


async def _job_executor(job):
    """Main job executor that dispatches to the appropriate handler."""
    job_id = job.id
    
    # Get job data from database
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == job_id).first()
        
        if not task:
            logger.error(f"Task not found in DB: {job_id}")
            return
        
        if task.status != TaskStatus.ACTIVE:
            logger.info(f"Task {task.task_name} is paused, skipping")
            return
        
        logger.info(f"Running scheduled task: {task.task_name}")
        
        # Execute based on task type
        if task.task_type == TaskType.STANDALONE_SH:
            if not task.script_path:
                logger.error(f"No script_path for standalone task: {job_id}")
                return
            
            status, output = await _execute_standalone_shell(
                job_id, task.script_path, task.notification_enabled
            )
        
        elif task.task_type == TaskType.SUBAGENT_RECALL:
            if not task.subagent_task:
                logger.error(f"No subagent_task for recall task: {job_id}")
                return
            
            status, output = await _execute_subagent_recall(
                job_id,
                task.subagent_name or f"scheduled_{job_id}",
                task.subagent_instructions or "You are a scheduled task executor.",
                task.subagent_task,
                task.notification_enabled
            )
        
        elif task.task_type == TaskType.TEAM_TASK:
            if not task.team_members or not task.subagent_task:
                logger.error(f"Missing members or task for team_task: {job_id}")
                return
            
            status, output = await _execute_team_task(
                job_id,
                task.task_name,
                task.team_members,
                task.subagent_task,
                task.notification_enabled
            )
        
        elif task.task_type == TaskType.SILENT:
            status, output = await _execute_silent_task(job_id)
        
        else:
            logger.error(f"Unknown task type: {task.task_type}")
            return
        
        # Update task status
        task.last_run = datetime.utcnow()
        task.last_status = status
        task.last_output = output[:5000] if output else None  # Truncate long outputs
        db.commit()
        
        # Send notification if enabled
        if task.notification_enabled and status == "failure":
            msg = f"⚠️ Scheduled task '{task.task_name}' failed: {output[:200]}"
            await _send_telegram_notification(msg)
        
        logger.info(f"Task {task.task_name} completed with status: {status}")
    
    except Exception as e:
        logger.error(f"Job execution failed: {e}")
    finally:
        db.close()


def _validate_cron(schedule: str) -> bool:
    """Validate a cron schedule string."""
    try:
        croniter(schedule)
        return True
    except Exception:
        return False


# ============================================================================
# PUBLIC API TOOLS
# ============================================================================

def add_scheduled_task(
    task_name: str,
    schedule: str,
    task_type: str,
    script_path: Optional[str] = None,
    subagent_name: Optional[str] = None,
    subagent_instructions: Optional[str] = None,
    subagent_task: Optional[str] = None,
    team_members: Optional[List[str]] = None,
    notification_enabled: bool = True
) -> str:
    """
    Add a new scheduled task.
    
    Args:
        task_name: Unique name for the task
        schedule: Cron expression (e.g., "0 * * * *" for hourly)
        task_type: One of "standalone_sh", "subagent_recall", "silent"
        script_path: Path to shell script (for standalone_sh)
        subagent_name: Name for subagent (for subagent_recall)
        subagent_instructions: Instructions for subagent (for subagent_recall)
        subagent_task: Task prompt for subagent (for subagent_recall)
        notification_enabled: Whether to send notifications
    
    Returns:
        Confirmation message
    """
    # Validate cron
    if not _validate_cron(schedule):
        return f"Error: Invalid cron expression: {schedule}"
    
    # Validate task type
    valid_types = ["standalone_sh", "subagent_recall", "team_task", "silent"]
    if task_type not in valid_types:
        return f"Error: Invalid task_type. Must be one of: {valid_types}"
    
    # Validate type-specific fields
    if task_type == "standalone_sh" and not script_path:
        return "Error: script_path required for standalone_sh task"
    
    if task_type == "subagent_recall" and not subagent_task:
        return "Error: subagent_task required for subagent_recall task"
    
    # Save to database
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Check for existing task
        existing = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        if existing:
            return f"Error: Task '{task_name}' already exists. Use update_scheduled_task to modify."
        
        # Create task
        task = ScheduledTask(
            task_name=task_name,
            schedule=schedule,
            task_type=TaskType(task_type),
            script_path=script_path,
            subagent_name=subagent_name,
            subagent_instructions=subagent_instructions,
            subagent_task=subagent_task,
            team_members=team_members,
            status=TaskStatus.ACTIVE,
            notification_enabled=notification_enabled
        )
        
        db.add(task)
        db.commit()
        db.refresh(task)
        
        # Add to scheduler
        scheduler = get_scheduler()
        scheduler.add_job(
            _job_executor,
            'cron',
            cron=schedule,
            id=str(task.id),
            replace_existing=True
        )
        
        logger.info(f"Added scheduled task: {task_name}")
        return f"✅ Task '{task_name}' added successfully. Schedule: {schedule}"
    
    except Exception as e:
        logger.error(f"Failed to add scheduled task: {e}")
        return f"Error: {e}"
    finally:
        db.close()


def list_scheduled_tasks() -> str:
    """List all scheduled tasks."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        tasks = db.query(ScheduledTask).order_by(ScheduledTask.id).all()
        
        if not tasks:
            return "No scheduled tasks found."
        
        lines = ["📅 **Scheduled Tasks**", ""]
        
        for task in tasks:
            lines.append(f"**ID: {task.id} | {task.task_name}**")
            lines.append(f"  Type: {task.task_type.value}")
            lines.append(f"  Schedule: {task.schedule}")
            lines.append(f"  Status: {task.status.value}")
            lines.append(f"  Notifications: {'On' if task.notification_enabled else 'Off'}")
            if task.last_run:
                lines.append(f"  Last Run: {task.last_run.strftime('%Y-%m-%d %H:%M:%S')} ({task.last_status})")
            lines.append("")
        
        return "\n".join(lines)
    
    finally:
        db.close()


def get_scheduled_task(task_name: str) -> str:
    """Get details of a specific scheduled task."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        
        if not task:
            return f"Error: Task '{task_name}' not found."
        
        lines = [f"**Task: {task.task_name}**", ""]
        lines.append(f"ID: {task.id}")
        lines.append(f"Type: {task.task_type.value}")
        lines.append(f"Schedule: {task.schedule}")
        lines.append(f"Status: {task.status.value}")
        lines.append(f"Notifications: {'Enabled' if task.notification_enabled else 'Disabled'}")
        
        if task.script_path:
            lines.append(f"Script: {task.script_path}")
        
        if task.subagent_name:
            lines.append(f"Subagent Name: {task.subagent_name}")
        
        if task.subagent_instructions:
            lines.append(f"Instructions: {task.subagent_instructions}")
        
        if task.subagent_task:
            lines.append(f"Task: {task.subagent_task}")
        
        if task.last_run:
            lines.append(f"Last Run: {task.last_run.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Last Status: {task.last_status}")
        
        if task.last_output:
            lines.append(f"Last Output:\n{task.last_output[:500]}")
        
        return "\n".join(lines)
    
    finally:
        db.close()


def update_scheduled_task(
    task_name: str,
    schedule: Optional[str] = None,
    script_path: Optional[str] = None,
    subagent_name: Optional[str] = None,
    subagent_instructions: Optional[str] = None,
    subagent_task: Optional[str] = None,
    notification_enabled: Optional[bool] = None
) -> str:
    """Update an existing scheduled task."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        
        if not task:
            return f"Error: Task '{task_name}' not found."
        
        # Validate cron if provided
        if schedule:
            if not _validate_cron(schedule):
                return f"Error: Invalid cron expression: {schedule}"
            task.schedule = schedule
        
        if script_path is not None:
            task.script_path = script_path
        
        if subagent_name is not None:
            task.subagent_name = subagent_name
        
        if subagent_instructions is not None:
            task.subagent_instructions = subagent_instructions
        
        if subagent_task is not None:
            task.subagent_task = subagent_task
        
        if notification_enabled is not None:
            task.notification_enabled = notification_enabled
        
        db.commit()
        
        # Update scheduler job
        scheduler = get_scheduler()
        try:
            scheduler.remove_job(str(task.id))
        except:
            pass
        
        if task.status == TaskStatus.ACTIVE:
            scheduler.add_job(
                _job_executor,
                'cron',
                cron=task.schedule,
                id=str(task.id),
                replace_existing=True
            )
        
        return f"✅ Task '{task_name}' updated successfully."
    
    except Exception as e:
        logger.error(f"Failed to update scheduled task: {e}")
        return f"Error: {e}"
    finally:
        db.close()


def remove_scheduled_task(task_name: str) -> str:
    """Remove a scheduled task."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        
        if not task:
            return f"Error: Task '{task_name}' not found."
        
        task_id = task.id
        db.delete(task)
        db.commit()
        
        # Remove from scheduler
        scheduler = get_scheduler()
        try:
            scheduler.remove_job(str(task_id))
        except:
            pass
        
        return f"✅ Task '{task_name}' removed successfully."
    
    except Exception as e:
        logger.error(f"Failed to remove scheduled task: {e}")
        return f"Error: {e}"
    finally:
        db.close()


def pause_scheduled_task(task_name: str) -> str:
    """Pause a scheduled task."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        
        if not task:
            return f"Error: Task '{task_name}' not found."
        
        task.status = TaskStatus.PAUSED
        db.commit()
        
        # Remove from scheduler
        scheduler = get_scheduler()
        try:
            scheduler.remove_job(str(task.id))
        except:
            pass
        
        return f"⏸️ Task '{task_name}' paused."
    
    except Exception as e:
        return f"Error: {e}"
    finally:
        db.close()


def resume_scheduled_task(task_name: str) -> str:
    """Resume a paused scheduled task."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        
        if not task:
            return f"Error: Task '{task_name}' not found."
        
        if task.status == TaskStatus.ACTIVE:
            return f"Task '{task_name}' is already active."
        
        task.status = TaskStatus.ACTIVE
        db.commit()
        
        # Add to scheduler
        scheduler = get_scheduler()
        scheduler.add_job(
            _job_executor,
            'cron',
            cron=task.schedule,
            id=str(task.id),
            replace_existing=True
        )
        
        return f"▶️ Task '{task_name}' resumed."
    
    except Exception as e:
        return f"Error: {e}"
    finally:
        db.close()


def run_scheduled_task_now(task_name: str) -> str:
    """Manually trigger a scheduled task immediately."""
    engine = get_db_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        
        if not task:
            return f"Error: Task '{task_name}' not found."
        
        # Create a one-time job
        scheduler = get_scheduler()
        job = scheduler.add_job(
            _job_executor,
            'date',
            run_date=datetime.utcnow(),
            id=f"manual_{task.id}"
        )
        
        return f"🚀 Task '{task_name}' triggered manually."
    
    except Exception as e:
        return f"Error: {e}"
    finally:
        db.close()


def start_scheduler() -> str:
    """Start the scheduler background service."""
    try:
        # Initialize database
        init_db()
        
        # Get scheduler
        scheduler = get_scheduler()
        
        if scheduler.running:
            return "Scheduler is already running."
        
        # Add existing active tasks from database
        engine = get_db_engine()
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        
        try:
            active_tasks = db.query(ScheduledTask).filter(
                ScheduledTask.status == TaskStatus.ACTIVE
            ).all()
            
            for task in active_tasks:
                scheduler.add_job(
                    _job_executor,
                    'cron',
                    cron=task.schedule,
                    id=str(task.id),
                    replace_existing=True
                )
                logger.info(f"Loaded task: {task.task_name}")
        
        finally:
            db.close()
        
        # Start scheduler
        scheduler.start()
        logger.info("Scheduler started successfully")
        
        return "✅ Scheduler started."
    
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
        return f"Error: {e}"


def stop_scheduler() -> str:
    """Stop the scheduler background service."""
    try:
        scheduler = get_scheduler()
        
        if not scheduler.running:
            return "Scheduler is not running."
        
        scheduler.shutdown()
        logger.info("Scheduler stopped")
        
        return "✅ Scheduler stopped."
    
    except Exception as e:
        return f"Error: {e}"


def get_scheduler_status() -> str:
    """Get scheduler runtime status."""
    try:
        scheduler = get_scheduler()
        
        if not scheduler.running:
            return "Scheduler: Stopped"
        
        jobs = scheduler.get_jobs()
        
        lines = [
            f"Scheduler: Running",
            f"Active Jobs: {len(jobs)}",
            ""
        ]
        
        for job in jobs:
            lines.append(f"  - {job.id}: next run at {job.next_run_time}")
        
        return "\n".join(lines)
    
    except Exception as e:
        return f"Error: {e}"


# ============================================================================
# SCHEDULER INITIALIZATION HELPER
# ============================================================================

def initialize_scheduler():
    """Initialize scheduler on application startup."""
    try:
        result = start_scheduler()
        logger.info(result)
        return True
    except Exception as e:
        logger.error(f"Scheduler initialization failed: {e}")
        return False