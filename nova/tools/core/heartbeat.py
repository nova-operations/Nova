"""
Heartbeat System for Nova PM Agent

This module provides automated heartbeat monitoring for subagents.
The Heartbeat Subagent periodically checks on active subagents and reports
their status back to the Project Manager (Nova).

Key Components:
- HeartbeatMonitor: Background task that polls subagent status
- register_for_heartbeat: Register a subagent to be monitored
- unregister_from_heartbeat: Stop monitoring a subagent
- get_heartbeat_status: Get current status of all monitored subagents
- start_heartbeat_monitor: Start the background heartbeat loop
- stop_heartbeat_monitor: Stop the heartbeat loop
"""

import asyncio
import logging
import time
from typing import Dict, Optional, List
from datetime import datetime
from dataclasses import dataclass, field

from nova.tools.agents.subagent import SUBAGENTS, list_subagents, get_subagent_result

logger = logging.getLogger(__name__)

# Configuration
HEARTBEAT_INTERVAL_SECONDS = 30  # Check every 30 seconds
HEARTBEAT_WARNING_THRESHOLD = (
    300  # Notify Nova if team running >5 min without completion
)
HEARTBEAT_FAILURE_NOTIFIED = (
    set()
)  # Track which failures we've already notified Nova about


@dataclass
class HeartbeatRecord:
    """Record of a heartbeat check for a subagent."""

    subagent_id: str
    name: str
    status: str
    last_check: float
    start_time: float
    chat_id: Optional[str] = None
    warning_issued: bool = False
    updates: List[str] = field(default_factory=list)
    result: Optional[str] = None


class HeartbeatMonitor:
    """
    Background monitor that periodically checks on active subagents.

    This acts as the "Heartbeat Subagent" - continuously running in the background,
    polling registered subagents and collecting their status.
    """

    def __init__(self, interval: int = HEARTBEAT_INTERVAL_SECONDS):
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._records: Dict[str, HeartbeatRecord] = {}
        self._callbacks: List[callable] = []

    def register_callback(self, callback: callable):
        """Add a callback to be called on every heartbeat check."""
        self._callbacks.append(callback)

    def register_subagent(
        self, subagent_id: str, name: str, chat_id: Optional[str] = None
    ):
        """Register a subagent for heartbeat monitoring."""
        self._records[subagent_id] = HeartbeatRecord(
            subagent_id=subagent_id,
            name=name,
            status="unknown",
            last_check=time.time(),
            start_time=time.time(),
            chat_id=chat_id,
        )
        logger.info(f"Heartbeat: Registered subagent {name} ({subagent_id})")

    def unregister_subagent(self, subagent_id: str):
        """Remove a subagent from heartbeat monitoring."""
        if subagent_id in self._records:
            name = self._records[subagent_id].name
            del self._records[subagent_id]
            logger.info(f"Heartbeat: Unregistered subagent {name} ({subagent_id})")

    async def _check_subagent(self, subagent_id: str) -> HeartbeatRecord:
        """Check the status of a single subagent."""
        if subagent_id not in SUBAGENTS:
            # Subagent no longer exists
            if subagent_id in self._records:
                record = self._records[subagent_id]
                record.status = "not_found"
                return record

        data = SUBAGENTS[subagent_id]

        # Get or create record
        if subagent_id not in self._records:
            self._records[subagent_id] = HeartbeatRecord(
                subagent_id=subagent_id,
                name=data.get("name", "unknown"),
                status=data.get("status", "unknown"),
                last_check=time.time(),
                start_time=time.time(),
                chat_id=data.get("chat_id"),
            )

        record = self._records[subagent_id]
        record.status = data.get("status", "unknown")
        record.last_check = time.time()

        # Check for warnings
        elapsed = time.time() - record.start_time
        if elapsed > HEARTBEAT_WARNING_THRESHOLD and record.status == "running":
            if not record.warning_issued:
                record.warning_issued = True
                record.updates.append(
                    f"⚠️ Warning: {record.name} running for {elapsed:.0f}s without completion"
                )

        # Capture result if completed
        if record.status in ["completed", "failed"]:
            record.result = data.get("result")

        # Add status update
        record.updates.append(
            f"[{datetime.now().strftime('%H:%M:%S')}] Status: {record.status}"
        )

        return record

    async def _heartbeat_loop(self):
        """Main heartbeat loop. Actively triggers Nova recovery on failures."""
        logger.info(f"Heartbeat Monitor started (interval: {self.interval}s)")

        while self._running:
            try:
                active_records = []
                for subagent_id in list(self._records.keys()):
                    record = await self._check_subagent(subagent_id)
                    active_records.append(record)

                # Smart recovery: notify Nova about failures/timeouts
                await self._trigger_nova_recovery(active_records)

                # Call registered callbacks (e.g. Telegram status updates)
                for callback in self._callbacks:
                    try:
                        callback(self._generate_report(active_records), active_records)
                    except Exception as e:
                        logger.error(f"Heartbeat callback error: {e}")

                # Cleanup terminal-state records
                to_remove = [
                    sid
                    for sid, rec in self._records.items()
                    if rec.status in ("completed", "failed", "cancelled", "not_found")
                ]
                for sid in to_remove:
                    del self._records[sid]

            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")

            await asyncio.sleep(self.interval)

        logger.info("Heartbeat Monitor stopped")

    async def _trigger_nova_recovery(self, records: list):
        """Wake Nova when a team fails or has been running too long."""
        import os

        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not chat_id:
            return

        try:
            from nova.telegram_bot import reinvigorate_nova
        except ImportError:
            return

        for record in records:
            sid = record.subagent_id

            # Don't double-notify
            if sid in HEARTBEAT_FAILURE_NOTIFIED:
                continue

            elapsed = time.time() - record.start_time

            if record.status == "failed":
                HEARTBEAT_FAILURE_NOTIFIED.add(sid)
                result_snippet = (
                    str(record.result)[:500] if record.result else "(no result)"
                )
                asyncio.create_task(
                    reinvigorate_nova(
                        chat_id,
                        f"SYSTEM_ALERT: Team/Agent '{record.name}' FAILED.\n"
                        f"Error: {result_snippet}\n"
                        f"Elapsed: {elapsed:.0f}s\n"
                        f"Decide: spawn Bug-Fixer team, or run parallel fix+alternative.",
                    )
                )

            elif record.status == "running" and elapsed > HEARTBEAT_WARNING_THRESHOLD:
                if not record.warning_issued:
                    record.warning_issued = True
                    HEARTBEAT_FAILURE_NOTIFIED.add(sid)
                    asyncio.create_task(
                        reinvigorate_nova(
                            chat_id,
                            f"SYSTEM_ALERT: Team/Agent '{record.name}' has been running "
                            f"for {elapsed:.0f}s without completing.\n"
                            f"Decide: let it continue, kill and retry, or spawn alternative.",
                        )
                    )

    def _generate_report(self, records: List[HeartbeatRecord]) -> str:
        """Generate a human-readable heartbeat report."""
        if not records:
            return "[OK] Heartbeat: No active subagents to monitor."

        lines = ["[RPT] **Heartbeat Report**"]
        lines.append(f"_{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
        lines.append("")

        running_count = 0
        completed_count = 0

        for record in records:
            if record.status == "running":
                running_count += 1
                elapsed = time.time() - record.start_time
                status_tag = "[WARN]" if record.warning_issued else "[BUSY]"
                lines.append(f"{status_tag} **{record.name}**: Running ({elapsed:.0f}s)")
            elif record.status == "completed":
                completed_count += 1
                lines.append(f"[OK] **{record.name}**: Completed")
            elif record.status == "failed":
                lines.append(f"[FAIL] **{record.name}**: Failed")
            elif record.status == "starting":
                lines.append(f"[WAIT] **{record.name}**: Starting...")
            elif record.status == "cancelled":
                lines.append(f"[STOP] **{record.name}**: Cancelled")

        lines.append("")
        lines.append(f"Summary: {running_count} running, {completed_count} completed")

        return "\n".join(lines)

    def start(self):
        """Start the heartbeat monitor."""
        if not self._running:
            self._running = True
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._heartbeat_loop())
            except RuntimeError:
                logger.warning(
                    "No running event loop - heartbeat will start when agent runs"
                )

    async def stop(self):
        """Stop the heartbeat monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_status(self) -> str:
        """Get current heartbeat status."""
        records = list(self._records.values())
        return self._generate_report(records)

    def get_detailed_status(self) -> Dict:
        """Get detailed status as a dictionary."""
        return {
            "running": self._running,
            "monitored_subagents": len(self._records),
            "records": {
                sid: {
                    "name": r.name,
                    "status": r.status,
                    "elapsed_seconds": time.time() - r.start_time,
                    "warning_issued": r.warning_issued,
                }
                for sid, r in self._records.items()
            },
        }


# Global heartbeat monitor instance
_heartbeat_monitor: Optional[HeartbeatMonitor] = None


def get_heartbeat_monitor() -> HeartbeatMonitor:
    """Get the global heartbeat monitor instance."""
    global _heartbeat_monitor
    if _heartbeat_monitor is None:
        _heartbeat_monitor = HeartbeatMonitor()
    return _heartbeat_monitor


# =============================================================================
# PUBLIC API FUNCTIONS
# =============================================================================


def start_heartbeat_monitor(interval_seconds: int = 30) -> str:
    """Start the heartbeat monitor. Auto-registers all running subagents."""
    monitor = get_heartbeat_monitor()
    monitor.interval = interval_seconds
    monitor.start()
    return f"Heartbeat Monitor started (checking every {interval_seconds}s)"


def stop_heartbeat_monitor() -> str:
    """Stop the heartbeat monitor."""
    monitor = get_heartbeat_monitor()
    monitor._running = False
    return "Heartbeat Monitor stop requested."


def register_subagent_for_heartbeat(
    subagent_id: str, name: str, chat_id: Optional[str] = None
) -> str:
    """Register a subagent/team for heartbeat monitoring."""
    monitor = get_heartbeat_monitor()
    monitor.register_subagent(subagent_id, name, chat_id=chat_id)
    return f"Registered '{name}' ({subagent_id}) for heartbeat."


def get_heartbeat_status() -> str:
    """Get current heartbeat status of all monitored agents."""
    return get_heartbeat_monitor().get_status()


def setup_heartbeat_for_task(subagent_ids: List[str], subagent_names: List[str]) -> str:
    """Convenience: register a batch of subagents for heartbeat monitoring."""
    monitor = get_heartbeat_monitor()
    monitor.start()
    for sid, name in zip(subagent_ids, subagent_names):
        chat_id = SUBAGENTS.get(sid, {}).get("chat_id")
        monitor.register_subagent(sid, name, chat_id=chat_id)
    return f"Heartbeat monitoring set up for {len(subagent_ids)} agents."
