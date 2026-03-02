"""
Nova Scheduler - Persistent Cron Task System

Provides DB-backed scheduling for:
- Standalone shell scripts
- Subagent recalls
- Silent background tasks

Supports notification via Telegram Webhooks and full CRUD operations.
"""

import os
import sys
import asyncio
import logging
import httpx
from datetime import datetime
from typing import Optional, Dict, Any, List
import enum
from croniter import croniter
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Text,
    Enum,
    JSON,
    text,
)
from sqlalchemy.orm import sessionmaker
from nova.db.base import Base
from nova.db.engine import get_db_engine, get_session_factory
from dotenv import load_dotenv
from nova.tools.core.context_optimizer import wrap_tool_output_optimization

load_dotenv()
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE SCHEMA
# ============================================================================


class TaskStatus(str, enum.Enum):
    """Task status enumeration."""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class TaskType(str, enum.Enum):
    """Task type enumeration."""

    STANDALONE_SH = "standalone_sh"
    SUBAGENT_RECALL = "subagent_recall"
    TEAM_TASK = "team_task"
    SILENT = "silent"
    ALERT = "alert"
    WATCHER = "watcher"
    INLINE_SCRIPT = "inline_script"


class ScheduledTask(Base):
    """Scheduled task database model."""

    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String(255), nullable=False, unique=True)
    schedule = Column(String(100), nullable=False)  # Cron format
    task_type = Column(String(50), nullable=False)
    script_path = Column(Text, nullable=True)  # Path to shell script
    subagent_name = Column(String(255), nullable=True)  # Name for subagent
    subagent_instructions = Column(
        Text, nullable=True
    )  # System instructions for subagent
    subagent_task = Column(Text, nullable=True)  # Task prompt for subagent
    team_members = Column(JSON, nullable=True)  # List of specialist names for TEAM_TASK
    status = Column(Enum(TaskStatus), default=TaskStatus.RUNNING)
    notification_enabled = Column(Boolean, default=True)
    target_chat_id = Column(String(100), nullable=True)  # Specific chat ID for alerts
    last_run = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)  # success, failure, skipped
    last_output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# DATABASE CONNECTION
# ============================================================================


def get_session():
    """Returns a new session from the current session factory."""
    return get_session_factory()()


# ============================================================================
# SCHEDULER IMPLEMENTATION
# ============================================================================

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None
_scheduler_initialized: bool = False


def _cleanup_all_orphaned_jobs():
    """
    Clean up all orphaned APScheduler jobs that have no corresponding DB record.
    This is called automatically on scheduler initialization to prevent stale job errors.
    """
    global _scheduler_initialized
    if _scheduler_initialized:
        return

    try:
        db = get_session()
        engine = get_db_engine()
        scheduler = get_scheduler()

        try:
            # Get job IDs directly from the database table (not in-memory)
            with engine.connect() as conn:
                result = conn.execute(text("SELECT id FROM apscheduler_jobs"))
                apscheduler_job_ids = {row[0] for row in result.fetchall()}

            # Get all task IDs from DB (including active and paused)
            db_tasks = db.query(ScheduledTask).all()
            db_task_ids = {str(task.id) for task in db_tasks}

            # Also handle manual trigger jobs (prefixed with "manual_")
            db_task_ids.update({f"manual_{t.id}" for t in db_tasks})

            # Find orphaned jobs (in APScheduler but not in DB)
            orphaned_jobs = apscheduler_job_ids - db_task_ids

            # Remove orphaned jobs directly from the database table
            for job_id in orphaned_jobs:
                try:
                    # Remove from in-memory scheduler if loaded
                    scheduler.remove_job(job_id)
                except Exception as e:
                    # Job might not be loaded in memory, that's ok
                    pass

                # Always try to remove from the database table
                try:
                    with engine.connect() as conn:
                        conn.execute(
                            text("DELETE FROM apscheduler_jobs WHERE id = :id"),
                            {"id": job_id},
                        )
                        conn.commit()
                    logger.info(f"Cleaned up orphaned APScheduler job: {job_id}")
                except Exception as e:
                    logger.debug(f"Failed to remove job {job_id} from DB: {e}")

        finally:
            db.close()

        _scheduler_initialized = True

    except Exception as e:
        logger.warning(f"Failed to cleanup orphaned jobs during init: {e}")
        # Still mark as initialized to prevent infinite retry
        _scheduler_initialized = True


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler

    if _scheduler is not None:
        return _scheduler

    # Create database engine for job store
    engine = get_db_engine()
    jobstores = {
        "default": SQLAlchemyJobStore(
            engine=engine, metadata=Base.metadata, tablename="apscheduler_jobs"
        )
    }

    # Create executor for running async jobs
    executors = {"default": AsyncIOExecutor()}

    _scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults={
            "coalesce": True,  # Combine missed runs into one
            "max_instances": 1,  # Only one instance at a time
            "misfire_grace_time": 300,  # 5 minutes grace period
        },
        timezone="UTC",
    )

    # Clean up orphaned jobs on first scheduler creation
    _cleanup_all_orphaned_jobs()

    return _scheduler


async def _send_telegram_notification(message: str, chat_id: Optional[str] = None):
    """Send notification via Telegram API Webhook."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    default_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    target_chat_id = chat_id or default_chat_id

    if not token or not target_chat_id:
        logger.error(f"Telegram credentials missing. Chat ID: {target_chat_id}")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": str(target_chat_id), "text": message}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                logger.info(f"Successfully sent notification to chat {target_chat_id}")
            else:
                logger.error(f"Telegram API failed: {response.text}")
    except Exception as e:
        logger.error(f"Failed to send telegram notification to {target_chat_id}: {e}")


async def _execute_standalone_shell(
    job_id: int, script_path: str, notification_enabled: bool
):
    """Execute a standalone shell script."""
    logger.info(f"Executing standalone script: {script_path}")

    try:
        # Run the shell script
        process = await asyncio.create_subprocess_shell(
            script_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")
        error = stderr.decode("utf-8", errors="replace")

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
    notification_enabled: bool,
    target_chat_id: Optional[str] = None,
):
    """Execute a subagent recall."""
    logger.info(f"Executing subagent recall: {subagent_name}")

    try:
        # Import here to avoid circular imports
        from nova.tools.agents.subagent import create_subagent

        # Create subagent asynchronously
        # Use the task's notification setting to determine silence
        result = await create_subagent(
            name=subagent_name,
            instructions=subagent_instructions,
            task=subagent_task,
            chat_id=target_chat_id,
            silent=not notification_enabled,
        )

        if result.startswith("Error"):
            return "failure", result

        # For recall tasks, we don't wait for the subagent to complete
        notification_msg = (
            f"[RUN] Subagent '{subagent_name}' triggered by scheduled task (ID: {job_id})"
        )

        if notification_enabled:
            await _send_telegram_notification(notification_msg, chat_id=target_chat_id)

        return "success", f"Subagent triggered: {result}"

    except Exception as e:
        logger.error(f"Failed to execute subagent recall: {e}")
        return "failure", str(e)


async def _execute_team_task(
    job_id: int,
    task_name: str,
    specialist_names: List[str],
    task_description: str,
    notification_enabled: bool,
    target_chat_id: Optional[str] = None,
):
    """Execute a team task recall."""
    logger.info(f"Executing scheduled team task: {task_name}")

    try:
        from nova.tools.agents.team_manager import run_team_task

        result = await run_team_task(
            task_name=task_name,
            specialist_names=specialist_names,
            task_description=task_description,
            chat_id=target_chat_id,
            silent=True,
        )

        if result.startswith("[FAIL]"):
            return "failure", result

        if notification_enabled:
            notification_msg = f"[TEAM] Task '{task_name}' ({len(specialist_names)} agents) triggered by schedule (ID: {job_id})"
            await _send_telegram_notification(notification_msg, chat_id=target_chat_id)

        return "success", result
    except Exception as e:
        logger.error(f"Failed to execute team task: {e}")
        return "failure", str(e)


async def _execute_silent_task(job_id: int):
    """Execute a silent task (no output/notification)."""
    logger.info(f"Executing silent task: {job_id}")
    return "success", "Silent task completed"


async def _execute_alert_task(
    job_id: int, alert_message: str, target_chat_id: Optional[str] = None
):
    """Execute an alert task (sends a direct message)."""
    logger.info(f"Executing alert task: {job_id} for chat: {target_chat_id}")
    await _send_telegram_notification(alert_message, chat_id=target_chat_id)
    return "success", f"Alert sent: {alert_message}"


async def _execute_watcher_task(
    job_id: int, script_content: str, target_chat_id: Optional[str] = None
):
    """Execute a WATCHER task and parse its output for __NOVA_TRIGGER__."""
    logger.info(f"Executing watcher task: {job_id}")

    if not script_content:
        return "failure", "No script content provided for watcher."

    try:
        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script_content)
            temp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

            output = result.stdout.strip()

            if "__NOVA_TRIGGER__" in output:
                # Extract payload: anything after __NOVA_TRIGGER__
                parts = output.split("__NOVA_TRIGGER__", 1)
                payload = (
                    parts[1].strip() if len(parts) > 1 else "Triggered without payload"
                )

                # Trigger reinvigorate_nova
                from nova.telegram_bot import reinvigorate_nova

                chat_id = target_chat_id or os.getenv("TELEGRAM_CHAT_ID")

                if chat_id:
                    trigger_msg = f"🔍 **Watcher Alert (Job {job_id}):**\n{payload}"
                    asyncio.create_task(reinvigorate_nova(chat_id, trigger_msg))
                    return "success", f"Triggered: {payload}"
                else:
                    logger.warning(
                        "Watcher triggered but no chat_id available to notify"
                    )
                    return "success", "Triggered but no chat_id"

            if result.returncode != 0:
                logger.error(f"Watcher script failed: {result.stderr}")
                return "failure", result.stderr

            return "success", "Completed silently (no trigger)"

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except Exception as e:
        logger.error(f"Failed to execute watcher task: {e}")
        return "failure", str(e)


async def _execute_inline_script(
    job_id: int,
    script_body: str,
    notification_enabled: bool,
    target_chat_id: Optional[str] = None,
):
    """
    Execute an inline script stored directly in the database.

    Language auto-detection order:
      1. First line: '#lang: python' | '#lang: sh' | '#lang: js'
      2. Shebang line (#!/.../python, #!/.../node, #!/bin/sh etc.)
      3. Default: python
    """
    import tempfile
    import subprocess

    logger.info(f"Executing inline_script task: job_id={job_id}")

    if not script_body:
        return "failure", "No script body provided."

    # --- Language detection ---
    lines = script_body.strip().splitlines()
    first_line = lines[0].strip().lower() if lines else ""

    lang = "python"  # default
    if first_line.startswith("#lang:"):
        detected = first_line.replace("#lang:", "").strip()
        if detected in ("sh", "shell", "bash"):
            lang = "sh"
        elif detected in ("js", "javascript", "node"):
            lang = "js"
        else:
            lang = "python"
        # Strip the #lang directive from the actual body we execute
        script_body = "\n".join(lines[1:])
    elif first_line.startswith("#!"):
        if "python" in first_line:
            lang = "python"
        elif "node" in first_line or "js" in first_line:
            lang = "js"
        elif any(s in first_line for s in ("sh", "bash", "zsh")):
            lang = "sh"

    # --- Choose interpreter and file extension ---
    if lang == "python":
        suffix = ".py"
        interpreter = [sys.executable]
    elif lang == "js":
        suffix = ".js"
        interpreter = ["node"]
    else:  # sh
        suffix = ".sh"
        interpreter = ["bash"]

    # --- Write to temp file and run ---
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
            f.write(script_body)
            temp_path = f.name

        try:
            result = subprocess.run(
                interpreter + [temp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            succeeded = result.returncode == 0

            if succeeded:
                output = stdout
                status = "success"
            else:
                output = f"[FAIL] Exit code {result.returncode}\n{stderr}\n{stdout}"
                status = "failure"

            # Notify if enabled
            if notification_enabled and output:
                snippet = output[:1000]
                await _send_telegram_notification(
                    f"[{lang.upper()}] Job {job_id} output:\n{snippet}",
                    chat_id=target_chat_id,
                )

            logger.info(f"Inline script completed: status={status}")
            return status, output

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except subprocess.TimeoutExpired:
        logger.error(f"Inline script timed out: job_id={job_id}")
        return "failure", "Script timed out after 120s."
    except FileNotFoundError as e:
        logger.error(f"Interpreter not found for lang={lang}: {e}")
        return "failure", f"Interpreter not found: {e}"
    except Exception as e:
        logger.error(f"Failed to execute inline script: {e}")
        return "failure", str(e)


def _cleanup_orphaned_job(job_id: str):
    """Remove an orphaned APScheduler job that has no corresponding DB task."""
    try:
        scheduler = get_scheduler()
        scheduler.remove_job(job_id)
        logger.info(f"Cleaned up orphaned APScheduler job: {job_id}")
    except Exception as e:
        logger.debug(f"Job {job_id} already removed or not found: {e}")

    # Also remove from database table directly
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM apscheduler_jobs WHERE id = :id"), {"id": job_id}
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"Failed to remove job {job_id} from DB table: {e}")


async def _job_executor(job_id: int):
    """Main job executor that dispatches to the appropriate handler."""
    # Get job data from database
    db = get_session()

    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == job_id).first()

        if not task:
            # Task not found in DB - this is an orphaned job in APScheduler
            # Clean it up to prevent future errors
            logger.debug(
                f"Task not found in DB: {job_id}. Cleaning up orphaned APScheduler job."
            )
            _cleanup_orphaned_job(str(job_id))
            return

        if task.status != TaskStatus.RUNNING:
            logger.info(f"Task {task.task_name} is paused, skipping")
            return

        logger.info(f"Running scheduled task: {task.task_name}")

        target_chat_id = task.target_chat_id

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
                task.notification_enabled,
                target_chat_id=target_chat_id,
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
                task.notification_enabled,
                target_chat_id=target_chat_id,
            )

        elif task.task_type == TaskType.SILENT:
            status, output = await _execute_silent_task(job_id)

        elif task.task_type == TaskType.ALERT:
            if not task.subagent_task:
                logger.error(f"No alert message for alert task: {job_id}")
                return
            status, output = await _execute_alert_task(
                job_id, task.subagent_task, target_chat_id=target_chat_id
            )

        elif task.task_type == TaskType.WATCHER:
            script_content = task.subagent_instructions or task.subagent_task
            if not script_content:
                logger.error(f"No script content for watcher task: {job_id}")
                return
            status, output = await _execute_watcher_task(
                job_id, script_content, target_chat_id=target_chat_id
            )

        elif task.task_type == TaskType.INLINE_SCRIPT:
            script_body = task.subagent_instructions or task.subagent_task
            if not script_body:
                logger.error(f"No script body for inline_script task: {job_id}")
                return
            status, output = await _execute_inline_script(
                job_id,
                script_body,
                task.notification_enabled,
                target_chat_id=target_chat_id,
            )

        else:
            logger.error(f"Unknown task type: {task.task_type}")
            return

        # Update task status
        task.last_run = datetime.utcnow()
        task.last_status = status
        task.last_output = output[:5000] if output else None  # Truncate long outputs
        db.commit()

        # Proactive Recovery: Wake up Nova on failure if notifications are enabled
        if task.notification_enabled and status == "failure":
            logger.info(
                f"Triggering proactive recovery for failed task: {task.task_name}"
            )
            chat_id = target_chat_id or os.getenv("TELEGRAM_CHAT_ID")
            if chat_id:
                try:
                    from nova.telegram_bot import reinvigorate_nova

                    fail_msg = f"⚠️ Scheduled task '{task.task_name}' (ID: {task.id}) failed.\nError: {output[:1000]}"
                    asyncio.create_task(reinvigorate_nova(chat_id, fail_msg))
                except Exception as ex:
                    logger.error(f"Failed to reinvigorate Nova for task failure: {ex}")
                    # Fallback to simple notification
                    await _send_telegram_notification(
                        f"⚠️ Scheduled task '{task.task_name}' failed: {output[:200]}",
                        chat_id=chat_id,
                    )
        elif (
            task.notification_enabled
            and status == "success"
            and task.task_type == TaskType.ALERT
        ):
            # Already handled in _execute_alert_task but good to have as record
            pass

        logger.info(f"Task {task.task_name} completed with status: {status}")

    except Exception as e:
        logger.error(f"Job execution failed: {e}")
        # PROACTIVE RECOVERY: Wake up Nova if a scheduled job hits a code/system error
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if chat_id:
            try:
                from nova.telegram_bot import reinvigorate_nova

                asyncio.create_task(
                    reinvigorate_nova(
                        chat_id,
                        f"🚨 Scheduler Engine Error (Job {job_id}): {str(e)}\n"
                        "Please check the scheduler logic or database schema.",
                    )
                )
            except Exception:
                pass
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


@wrap_tool_output_optimization
def add_scheduled_task(
    task_name: str,
    schedule: str,
    task_type: str,
    script_path: Optional[str] = None,
    subagent_name: Optional[str] = None,
    subagent_instructions: Optional[str] = None,
    subagent_task: Optional[str] = None,
    team_members: Optional[List[str]] = None,
    notification_enabled: bool = True,
    verbose: Optional[bool] = None,
    alert_message: Optional[str] = None,
    chat_id: Optional[str] = None,
    run_immediately: bool = True,
) -> str:
    """
    Add a new scheduled task.

    Args:
        task_name: Unique name for the task
        schedule: Cron expression (e.g., "0 * * * *" for hourly)
        task_type: One of "standalone_sh", "subagent_recall", "team_task", "silent",
                   "alert", "inline_script"
        script_path: Path to shell script (for standalone_sh)
        subagent_name: Display name (for subagent_recall)
        subagent_instructions: For subagent_recall: system prompt.
                               For inline_script: THE SCRIPT BODY (Python/Shell/JS).
                               First line may be '#lang: python|sh|js' to pick runtime;
                               defaults to Python if omitted.
        subagent_task: Task prompt for subagent_recall, or alert message for alert.
        notification_enabled: Whether to send notifications (verbose mode).
        verbose: Alias for notification_enabled.
        alert_message: Alias for subagent_task for alert type.
        chat_id: Optional specific chat ID to send notifications to.
        run_immediately: If True (default), run the task once right after creation.
                         Set to False only if you want to wait for the first cron tick.

    Returns:
        Confirmation message.
    """
    # Use verbose if provided as an alias for notification_enabled
    if verbose is not None:
        notification_enabled = verbose
    # Validate cron
    if not _validate_cron(schedule):
        return f"Error: Invalid cron expression: {schedule}"

    # Guard against accidentally scheduling every-single-minute (almost always wrong).
    # '* * * * *' fires 1440 times per day. If you truly need per-minute, use '*/1 * * * *'
    # explicitly for alert types. For other types the minimum useful interval is every 5 min.
    if schedule.strip() == "* * * * *" and task_type not in ("alert",):
        return (
            "Error: Schedule '* * * * *' (every minute) is not allowed for this task type. "
            "This almost always means the task should be executed ONCE as a direct action, not scheduled. "
            "If you genuinely need per-minute recurrence, use '*/1 * * * *' and task_type='alert'."
        )

    # Validate task type
    valid_types = ["standalone_sh", "subagent_recall", "team_task", "silent", "alert", "inline_script"]
    if task_type not in valid_types:
        return f"Error: Invalid task_type. Must be one of: {valid_types}"

    # Validate type-specific fields
    if task_type == "standalone_sh" and not script_path:
        return "Error: script_path required for standalone_sh task"

    if task_type == "subagent_recall" and not subagent_task:
        return "Error: subagent_task required for subagent_recall task"

    if task_type == "inline_script" and not subagent_instructions:
        return "Error: subagent_instructions required for inline_script (the script body goes there)"

    if task_type == "alert":
        if not subagent_task and not alert_message:
            return "Error: alert_message required for alert task"
        # Use alert_message if provided, otherwise fallback to subagent_task
        subagent_task = alert_message or subagent_task

    # Default to global chat_id if not provided
    if not chat_id:
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

    # Save to database

    db = get_session()

    try:
        # Check for existing task
        existing = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )
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
            status=TaskStatus.RUNNING,
            notification_enabled=notification_enabled,
            target_chat_id=chat_id,
        )

        db.add(task)
        db.commit()
        db.refresh(task)

        # Add to scheduler
        scheduler = get_scheduler()
        scheduler.add_job(
            _job_executor,
            trigger=CronTrigger.from_crontab(schedule),
            id=str(task.id),
            args=[task.id],
            replace_existing=True,
        )

        logger.info(f"Added scheduled task: {task_name}")

        # Fire immediately — use APScheduler's date trigger so it executes inside the
        # AsyncIOScheduler's own event loop, which works correctly from any thread
        # (including Agno's thread pool executor where tool calls run).
        if run_immediately:
            try:
                from datetime import datetime, timedelta
                scheduler.add_job(
                    _job_executor,
                    trigger="date",
                    run_date=datetime.utcnow() + timedelta(seconds=1),
                    args=[task.id],
                    id=f"immediate_{task.id}",
                    replace_existing=True,
                )
                logger.info(f"Immediate first run queued for '{task_name}'")
            except Exception as e:
                logger.warning(f"Could not queue immediate run of '{task_name}': {e}")

        return f"[OK] '{task_name}' scheduled (running every {schedule})."

    except Exception as e:
        logger.error(f"Failed to add scheduled task: {e}")
        return f"Error: {e}"
    finally:
        db.close()


@wrap_tool_output_optimization
def list_scheduled_tasks() -> str:
    """List all scheduled tasks."""

    db = get_session()

    try:
        tasks = db.query(ScheduledTask).order_by(ScheduledTask.id).all()

        if not tasks:
            return "No scheduled tasks found."

        lines = ["[SCH] Scheduled Tasks", ""]

        for task in tasks:
            lines.append(f"**ID: {task.id} | {task.task_name}**")
            lines.append(f"  Type: {task.task_type}")
            lines.append(f"  Schedule: {task.schedule}")
            lines.append(f"  Status: {task.status.value}")
            lines.append(
                f"  Notifications: {'On' if task.notification_enabled else 'Off'}"
            )
            if task.target_chat_id:
                lines.append(f"  Target Chat: {task.target_chat_id}")
            if task.last_run:
                lines.append(
                    f"  Last Run: {task.last_run.strftime('%Y-%m-%d %H:%M:%S')} ({task.last_status})"
                )
            lines.append("")

        return "\n".join(lines)

    finally:
        db.close()


@wrap_tool_output_optimization
def get_scheduled_task(task_name: str) -> str:
    """Get details of a specific scheduled task."""

    db = get_session()

    try:
        task = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )

        if not task:
            return f"Error: Task '{task_name}' not found."

        lines = [f"**Task: {task.task_name}**", ""]
        lines.append(f"ID: {task.id}")
        lines.append(f"Type: {task.task_type}")
        lines.append(f"Schedule: {task.schedule}")
        lines.append(f"Status: {task.status.value}")
        lines.append(
            f"Notifications: {'Enabled' if task.notification_enabled else 'Disabled'}"
        )
        if task.target_chat_id:
            lines.append(f"Target Chat: {task.target_chat_id}")

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


@wrap_tool_output_optimization
def update_scheduled_task(
    task_name: str,
    schedule: Optional[str] = None,
    task_type: Optional[str] = None,
    script_path: Optional[str] = None,
    subagent_name: Optional[str] = None,
    subagent_instructions: Optional[str] = None,
    subagent_task: Optional[str] = None,
    team_members: Optional[List[str]] = None,
    notification_enabled: Optional[bool] = None,
    verbose: Optional[bool] = None,
    alert_message: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> str:
    """
    Update an existing scheduled task.

    Args:
        task_name: Name of the task to update
        schedule: New cron schedule
        task_type: New task type
        script_path: New script path
        subagent_name: New subagent name
        subagent_instructions: New subagent instructions
        subagent_task: New subagent task / alert message
        team_members: New team members
        notification_enabled: Enable/disable notifications
        verbose: Alias for notification_enabled
        alert_message: Alias for subagent_task for alert type
        chat_id: New chat ID
    """

    db = get_session()

    try:
        task = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )

        if not task:
            return f"Error: Task '{task_name}' not found."

        # Validate cron if provided
        if schedule:
            if not _validate_cron(schedule):
                return f"Error: Invalid cron expression: {schedule}"
            task.schedule = schedule

        if task_type is not None:
            valid_types = [
                "standalone_sh",
                "subagent_recall",
                "team_task",
                "silent",
                "alert",
            ]
            if task_type not in valid_types:
                return f"Error: Invalid task_type. Must be one of: {valid_types}"
            task.task_type = TaskType(task_type)

        if script_path is not None:
            task.script_path = script_path

        if subagent_name is not None:
            task.subagent_name = subagent_name

        if subagent_instructions is not None:
            task.subagent_instructions = subagent_instructions

        if subagent_task is not None:
            task.subagent_task = subagent_task

        if team_members is not None:
            task.team_members = team_members
        # verbose is an alias for notification_enabled
        if verbose is not None:
            task.notification_enabled = verbose
        if notification_enabled is not None:
            task.notification_enabled = notification_enabled

        # alert_message is an alias for subagent_task for alert type
        if alert_message is not None:
            task.subagent_task = alert_message

        if chat_id is not None:
            task.target_chat_id = chat_id

        db.commit()

        # Update scheduler job
        scheduler = get_scheduler()
        try:
            scheduler.remove_job(str(task.id))
        except:
            pass

        if task.status == TaskStatus.RUNNING:
            scheduler.add_job(
                _job_executor,
                trigger=CronTrigger.from_crontab(task.schedule),
                id=str(task.id),
                args=[task.id],
                replace_existing=True,
            )

        return f"[OK] Task '{task_name}' updated successfully."

    except Exception as e:
        logger.error(f"Failed to update scheduled task: {e}")
        return f"Error: {e}"
    finally:
        db.close()


@wrap_tool_output_optimization
def remove_scheduled_task(task_name: str) -> str:
    """Remove a scheduled task."""

    db = get_session()

    try:
        task = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )

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


@wrap_tool_output_optimization
def pause_scheduled_task(task_name: str) -> str:
    """Pause a scheduled task."""

    db = get_session()

    try:
        task = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )

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

        return f"[PAUSED] Task '{task_name}' paused."

    except Exception as e:
        return f"Error: {e}"
    finally:
        db.close()


@wrap_tool_output_optimization
def resume_scheduled_task(task_name: str) -> str:
    """Resume a paused scheduled task."""

    db = get_session()

    try:
        task = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )

        if not task:
            return f"Error: Task '{task_name}' not found."

        if task.status == TaskStatus.RUNNING:
            return f"Task '{task_name}' is already active."

        task.status = TaskStatus.RUNNING
        db.commit()

        # Add to scheduler
        scheduler = get_scheduler()
        scheduler.add_job(
            _job_executor,
            trigger=CronTrigger.from_crontab(task.schedule),
            id=str(task.id),
            args=[task.id],
            replace_existing=True,
        )

        return f"[RESUMED] Task '{task_name}' resumed."

    except Exception as e:
        return f"Error: {e}"
    finally:
        db.close()


@wrap_tool_output_optimization
def run_scheduled_task_now(task_name: str) -> str:
    """Manually trigger a scheduled task immediately."""

    db = get_session()

    try:
        task = (
            db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
        )

        if not task:
            return f"Error: Task '{task_name}' not found."

        # Create a one-time job
        scheduler = get_scheduler()
        job = scheduler.add_job(
            _job_executor,
            "date",
            run_date=datetime.utcnow(),
            id=f"manual_{task.id}",
            args=[task.id],
        )

        return f"🚀 Task '{task_name}' triggered manually."

    except Exception as e:
        return f"Error: {e}"
    finally:
        db.close()


def sync_scheduler_with_db() -> str:
    """
    Synchronize APScheduler jobs with the database.
    Removes orphaned jobs that no longer have corresponding DB records.
    """
    scheduler = get_scheduler()
    db = get_session()
    engine = get_db_engine()

    try:
        # Get all APScheduler jobs directly from the database table
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id FROM apscheduler_jobs"))
            apscheduler_job_ids = {row[0] for row in result.fetchall()}

        # Get ALL tasks from DB (active and paused) for orphaned job detection
        all_tasks = db.query(ScheduledTask).all()
        all_task_ids = {str(task.id) for task in all_tasks}

        # Also handle manual trigger jobs (prefixed with "manual_")
        all_task_ids.update({f"manual_{t.id}" for t in all_tasks})

        # Get only active tasks for missing job addition
        active_tasks = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.status == TaskStatus.RUNNING)
            .all()
        )
        active_task_ids = {str(task.id) for task in active_tasks}

        # Find orphaned jobs (in APScheduler but not in DB)
        orphaned_jobs = apscheduler_job_ids - all_task_ids

        # Find missing jobs (in DB but not in APScheduler) - only active ones
        missing_jobs = active_task_ids - apscheduler_job_ids

        # Remove orphaned jobs
        removed_count = 0
        for job_id in orphaned_jobs:
            try:
                # Remove from in-memory scheduler if loaded
                scheduler.remove_job(job_id)
            except Exception as e:
                pass  # Job might not be in memory

            # Remove from database table
            try:
                with engine.connect() as conn:
                    conn.execute(
                        text("DELETE FROM apscheduler_jobs WHERE id = :id"),
                        {"id": job_id},
                    )
                    conn.commit()
                logger.info(f"Removed orphaned job: {job_id}")
                removed_count += 1
            except Exception as e:
                logger.debug(f"Failed to remove job {job_id}: {e}")

        # Add missing jobs
        added_count = 0
        for task in active_tasks:
            if str(task.id) in missing_jobs:
                try:
                    scheduler.add_job(
                        _job_executor,
                        trigger=CronTrigger.from_crontab(task.schedule),
                        id=str(task.id),
                        args=[task.id],
                        replace_existing=True,
                    )
                    logger.info(f"Added missing job for task: {task.task_name}")
                    added_count += 1
                except Exception as e:
                    logger.error(f"Failed to add job for task {task.task_name}: {e}")

        return "[OK] Sync complete. Removed {removed_count} orphaned jobs, added {added_count} missing jobs."

    except Exception as e:
        logger.error(f"Scheduler sync failed: {e}")
        return f"Error during sync: {e}"
    finally:
        db.close()


@wrap_tool_output_optimization
def start_scheduler() -> str:
    """Start the scheduler background service."""
    try:
        # Initialize database
        from migrations.migrate import run_migrations

        run_migrations()

        # Get scheduler (this triggers cleanup of orphaned jobs)
        scheduler = get_scheduler()

        if scheduler.running:
            return "Scheduler is already running."

        # Sync scheduler with database first (cleanup orphaned jobs)
        sync_result = sync_scheduler_with_db()
        logger.info(f"Scheduler sync: {sync_result}")

        # Add existing active tasks from database
        db = get_session()

        try:
            active_tasks = (
                db.query(ScheduledTask)
                .filter(ScheduledTask.status == TaskStatus.RUNNING)
                .all()
            )

            for task in active_tasks:
                scheduler.add_job(
                    _job_executor,
                    trigger=CronTrigger.from_crontab(task.schedule),
                    id=str(task.id),
                    args=[task.id],
                    replace_existing=True,
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


@wrap_tool_output_optimization
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


@wrap_tool_output_optimization
def get_scheduler_status() -> str:
    """Get scheduler runtime status."""
    try:
        scheduler = get_scheduler()

        if not scheduler.running:
            return "Scheduler: Stopped"

        jobs = scheduler.get_jobs()

        lines = [f"Scheduler: Running", f"Active Jobs: {len(jobs)}", ""]

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
