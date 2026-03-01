import logging
import asyncio
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
import enum
from typing import Optional

from nova.db.base import Base
from nova.db.engine import get_session_factory, get_db_engine
from nova.tools.subagent import create_subagent


class ErrorStatus(str, enum.Enum):
    NEW = "new"
    FIXING = "fixing"
    RESOLVED = "resolved"
    FAILED = "failed"
    IGNORED = "ignored"


class SystemErrorLog(Base):
    __tablename__ = "system_error_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    logger_name = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=False)
    traceback = Column(Text, nullable=True)
    status = Column(Enum(ErrorStatus), default=ErrorStatus.NEW)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ErrorBusHandler(logging.Handler):
    """Logging handler that captures ERROR and CRITICAL logs to the database."""

    def __init__(self):
        super().__init__()
        # Avoid recursive logging by keeping track of inside_handler
        self._inside = False

    def emit(self, record):
        if self._inside:
            return
        if record.levelno < logging.ERROR:
            return

        # Ignore errors from the error bus itself or subagent creation
        if record.name in (
            "nova.tools.error_bus",
            "nova.tools.subagent",
            "nova.tools.scheduler",
            "httpx",
            "telegram",
            "agno",
            "openai",
        ):
            return

        # Ignore transient API errors and database connection errors to prevent recursive loops
        msg = record.getMessage()
        if any(
            p in msg
            for p in [
                "Internal Server Error",
                "Bad Gateway",
                "Bad gateway",
                "502",
                "Rate limit",
                "Timeout",
                "too many clients",
                "connection failure",
                "OperationalError",
            ]
        ):
            return

        self._inside = True
        try:
            Session = get_session_factory()
            db = Session()
            try:
                msg = self.format(record)
                tb = record.exc_text if record.exc_info else None

                # Check for rate limiting / dupes
                recent = (
                    db.query(SystemErrorLog)
                    .filter(
                        SystemErrorLog.error_message == msg,
                        SystemErrorLog.status.in_(
                            [ErrorStatus.NEW, ErrorStatus.FIXING]
                        ),
                    )
                    .first()
                )
                if not recent:
                    new_err = SystemErrorLog(
                        logger_name=record.name,
                        error_message=msg,
                        traceback=tb,
                        status=ErrorStatus.NEW,
                    )
                    db.add(new_err)
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass  # Fail silently to not crash the app
        finally:
            self._inside = False


_error_monitor_task = None


async def _error_monitor_loop():
    """Background task that watches for new errors and spawns healing subagents."""
    logger = logging.getLogger("nova.tools.error_bus")
    while True:
        try:
            Session = get_session_factory()
            db = Session()
            try:
                # CRITICAL: We only process ONE error at a time for auto-healing
                # to prevent multiple subagents from fighting over codebase fixes.
                new_errors = (
                    db.query(SystemErrorLog)
                    .filter(SystemErrorLog.status == ErrorStatus.NEW)
                    .order_by(SystemErrorLog.id.desc())
                    .limit(1)
                    .all()
                )
                for err in new_errors:
                    # Additional safety: check if we already tried to fix this error many times
                    # (In a real system we'd have a retry_count column, but for now we use status logic)

                    logger.info(
                        f"Proactively fixing error ID {err.id}: {err.error_message[:100]}..."
                    )
                    err.status = ErrorStatus.FIXING
                    db.commit()

                    # Trigger a healing subagent with STRICT safety requirements
                    prompt = (
                        f"An error occurred in the system:\nLogger: {err.logger_name}\nError: {err.error_message}\nTraceback: {err.traceback}\n\n"
                        "### MANDATORY SAFETY PROTOCOL:\n"
                        "1. DIAGNOSE the failure by reading relevant code and logs.\n"
                        "2. IMPLEMENT a fix, but BEFORE writing any code, plan how to verify it.\n"
                        "3. AFTER editing any .py file, you MUST run `python3 -m py_compile ` to ensure NO syntax errors were introduced.\n"
                        "4. If the compilation fails, FIX the code immediately before reporting completion.\n"
                        "5. VERIFY the fix with a small test script if possible.\n\n"
                        "Please self-heal the system, fix the codebase, and verify the fix."
                    )

                    try:
                        # Spawn subagent
                        # We use a 10 min timeout for healer subagents to prevent hanging
                        result = await create_subagent(
                            name=f"auto_healer_{err.id}",
                            instructions="You are Nova's Auto-Healer. You are an expert at debugging and fixing technical issues safely. You prioritize system integrity and never leave files with syntax errors.",
                            task=prompt,
                        )
                        err.status = ErrorStatus.RESOLVED
                        logger.info(f"Auto-healer finished for error {err.id}.")
                    except Exception as e:
                        err.status = ErrorStatus.FAILED
                        logger.error(f"Auto-healer failed for error {err.id}: {e}")

                        # VIBRATE: Wake up Nova PM if auto-healing fails or for critical alerts
                        from nova.telegram_bot import reinvigorate_nova
                        import os

                        chat_id = os.getenv("TELEGRAM_CHAT_ID")
                        if chat_id:
                            # Only reinvigorate for non-transient failures
                            if "too many clients" not in str(e).lower():
                                await reinvigorate_nova(
                                    chat_id,
                                    f"🚨 CRITICAL SYSTEM ERROR: Auto-healer failed for error {err.id}.\n"
                                    f"Error: {err.error_message}\n"
                                    f"Healing Exception: {str(e)}\n\n"
                                    "Nova, I need manual intervention or higher-level reasoning to resolve this.",
                                )

                    db.commit()
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            # If we hit "too many clients" here, we should sleep longer
            if "too many clients" in str(e).lower():
                logger.warning("DB is full, sleeping longer...")
                await asyncio.sleep(60)
            else:
                logger.error(f"Error monitor loop exception: {e}")

        await asyncio.sleep(30)  # check every 30 seconds (increased from 10s)


def start_error_bus():
    """Initialize the error bus and monitor."""
    engine = get_db_engine()
    Base.metadata.create_all(engine, tables=[SystemErrorLog.__table__])

    # Add handler if not exists
    root_logger = logging.getLogger()
    if not any(isinstance(h, ErrorBusHandler) for h in root_logger.handlers):
        # We also want to format the message simply
        handler = ErrorBusHandler()
        handler.setLevel(logging.ERROR)
        root_logger.addHandler(handler)
        logging.getLogger("nova.tools.error_bus").info(
            "ErrorBus logging handler installed."
        )

    global _error_monitor_task
    if _error_monitor_task is None or _error_monitor_task.done():
        loop = asyncio.get_event_loop()
        _error_monitor_task = loop.create_task(_error_monitor_loop())
        logging.getLogger("nova.tools.error_bus").info("ErrorBus monitor started.")


def stop_error_bus():
    """Stop the error bus monitor and remove handler."""
    global _error_monitor_task
    if _error_monitor_task and not _error_monitor_task.done():
        _error_monitor_task.cancel()
        _error_monitor_task = None

    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        if isinstance(h, ErrorBusHandler):
            root_logger.removeHandler(h)