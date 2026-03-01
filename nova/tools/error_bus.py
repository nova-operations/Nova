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

        # Filter un-fixable LLM hallucination noise (agno runtime tool-call failures)
        msg = record.getMessage()
        if any(
            p in msg
            for p in [
                "Function RAG not found",
                "Function grep not found",
                "Function Glob not found",
                "Function ls not found",
                "Function execute_shell_command not found",
                "Could not run function write_file",
                "Missing required argument",
                "Function web_search not found",
                "Function list_files not found",
                "Function list_directory not found",
                "Function list_files_under_directory not found",
                "Function get_current_directory not found",
                "Function read_file not found",
                "Function write_file not found",
                "Function bug-fixer:bash not found",
                "Function Bash not found",
                "bug-fixer:list_files not found",
                "bug-fixer:read_file not found",
                "bug-fixer:write_file not found",
                "bug-fixer:execute_shell_command not found",
                "Function run_bash_command not found",
                "Function bug-fixer:diagnose_and_fix_bug not found",
            ]
        ):
            # Log at WARNING so humans can see it, but don't trigger the healer loop
            logging.getLogger("nova.tools.error_bus").warning(
                f"[TOOL-MISS] LLM called a hallucinated tool: {msg[:120]}"
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
                        from nova.tools.team_manager import run_team

                        chat_id = os.getenv("TELEGRAM_CHAT_ID")
                        # Use the real team runner — specialists have actual tools
                        result = await run_team(
                            task_name=f"auto_heal_error_{err.id}",
                            specialist_names=["Bug-Fixer", "Tester"],
                            task_description=task_description,
                            chat_id=chat_id,
                        )
                        err.status = ErrorStatus.RESOLVED
                        logger.info(f"Auto-healer team launched for error {err.id}: {result}")
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