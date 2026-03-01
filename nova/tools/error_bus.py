import logging
import asyncio
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
import enum
from typing import Optional

from nova.db.base import Base
from nova.db.engine import get_session_factory, get_db_engine


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

        # Ignore errors from the error bus itself or high-noise internal sources
        if record.name in (
            "nova.tools.error_bus",
            "nova.tools.scheduler",
            "nova.tools.heartbeat",
            "httpx",
            "telegram",
            "openai",
        ):
            return

        # Filter un-fixable LLM hallucination noise with a single regex.
        # Matches any 'Function X not found' (including namespaced member:tool forms)
        # and 'Could not run function X' Agno framework messages.
        import re
        msg = record.getMessage()
        if re.search(
            r"(Function [\w:\-]+ not found|Could not run function [\w_]+|Missing required argument|validation error for run_team|Unexpected keyword argument)",
            msg,
        ):
            logging.getLogger("nova.tools.error_bus").warning(
                f"[TOOL-MISS] {msg[:120]}"
            )
            return

        # Also filter startup-timing errors (specialists seeded after error bus starts)
        if any(
            p in msg
            for p in [
                "Specialist 'Tester' not found. Available: No specialists registered",
                "Specialist 'Bug-Fixer' not found. Available: No specialists registered",
            ]
        ):
            logging.getLogger("nova.tools.error_bus").warning(
                f"[STARTUP] Specialist not ready yet, ignoring: {msg[:120]}"
            )
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
    """Background task that watches for new errors and spawns healing teams."""
    logger = logging.getLogger("nova.tools.error_bus")
    while True:
        try:
            Session = get_session_factory()
            db = Session()
            try:
                # Process ONE error at a time to avoid healer conflicts
                new_errors = (
                    db.query(SystemErrorLog)
                    .filter(SystemErrorLog.status == ErrorStatus.NEW)
                    .order_by(SystemErrorLog.id.desc())
                    .limit(1)
                    .all()
                )
                for err in new_errors:
                    logger.info(
                        f"Proactively fixing error ID {err.id}: {err.error_message[:100]}..."
                    )
                    err.status = ErrorStatus.FIXING
                    db.commit()

                    task_description = (
                        f"SYSTEM ERROR — auto-heal required.\n"
                        f"Logger: {err.logger_name}\n"
                        f"Error: {err.error_message}\n"
                        f"Traceback: {err.traceback or 'N/A'}\n\n"
                        "### MANDATORY SAFETY PROTOCOL:\n"
                        "1. Read the relevant source file(s) to understand the failure.\n"
                        "2. Implement a targeted fix. Do NOT change unrelated code.\n"
                        "3. After editing any .py file, run `python3 -m py_compile <file>` to verify no syntax errors.\n"
                        "4. If compilation fails, fix it immediately.\n"
                        "5. Run the relevant test(s) if possible.\n"
                        "6. Push the fix using push_to_github().\n"
                    )

                    try:
                        import os
                        from nova.telegram_bot import reinvigorate_nova

                        chat_id = os.getenv("TELEGRAM_CHAT_ID")
                        if chat_id:
                            # Route through Nova (orchestrator) — it properly delegates
                            # to Bug-Fixer/Tester using run_team without the namespaced
                            # tool lookup bug that plagues direct Team invocations.
                            await reinvigorate_nova(
                                chat_id,
                                f"[AUTO-HEAL] System error detected.\n"
                                f"Logger: {err.logger_name}\n"
                                f"Error: {err.error_message[:400]}\n"
                                f"Traceback: {err.traceback[:300] if err.traceback else 'N/A'}\n\n"
                                "Diagnose the failure, fix the code, run tests, and push. "
                                "Use run_team(['Bug-Fixer', 'Tester'], ...) to delegate.",
                            )
                            err.status = ErrorStatus.RESOLVED
                            logger.info(f"Nova notified to heal error {err.id}")
                        else:
                            logger.warning(f"No TELEGRAM_CHAT_ID set, cannot notify Nova for error {err.id}")
                            err.status = ErrorStatus.FAILED
                    except Exception as e:
                        err.status = ErrorStatus.FAILED
                        logger.error(f"Auto-healer FAILED to launch for error {err.id}: {e}")

                        # Wake Nova PM directly for manual intervention
                        try:
                            from nova.telegram_bot import reinvigorate_nova
                            import os

                            chat_id = os.getenv("TELEGRAM_CHAT_ID")
                            if chat_id:
                                await reinvigorate_nova(
                                    chat_id,
                                    f"[CRIT] Auto-healer failed for error {err.id}.\n"
                                    f"Original error: {err.error_message[:300]}\n"
                                    f"Healer exception: {str(e)}\n\n"
                                    "Manual intervention required.",
                                )
                        except Exception:
                            pass

                    db.commit()
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            if "too many clients" in str(e).lower():
                logger.warning("DB is full, sleeping longer...")
                await asyncio.sleep(60)
            else:
                logger.error(f"Error monitor loop exception: {e}")

        await asyncio.sleep(30)


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