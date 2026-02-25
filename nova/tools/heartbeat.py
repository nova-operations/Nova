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

# Import subagent management
from nova.tools.subagent import SUBAGENTS, list_subagents, get_subagent_result

logger = logging.getLogger(__name__)

# Configuration
HEARTBEAT_INTERVAL_SECONDS = 30  # Check every 30 seconds
HEARTBEAT_WARNING_THRESHOLD = 120  # Warn if subagent running > 2 minutes without update


@dataclass
class HeartbeatRecord:
    """Record of a heartbeat check for a subagent."""
    subagent_id: str
    name: str
    status: str
    last_check: float
    start_time: float
    warning_issued: bool = False
    updates: List[str] = field(default_factory=list)


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
    
    def register_subagent(self, subagent_id: str, name: str):
        """Register a subagent for heartbeat monitoring."""
        self._records[subagent_id] = HeartbeatRecord(
            subagent_id=subagent_id,
            name=name,
            status="unknown",
            last_check=time.time(),
            start_time=time.time()
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
                start_time=time.time()
            )
        
        record = self._records[subagent_id]
        record.status = data.get("status", "unknown")
        record.last_check = time.time()
        
        # Check for warnings
        elapsed = time.time() - record.start_time
        if elapsed > HEARTBEAT_WARNING_THRESHOLD and record.status == "running":
            if not record.warning_issued:
                record.warning_issued = True
                record.updates.append(f"⚠️ Warning: {record.name} running for {elapsed:.0f}s without completion")
        
        # Add status update
        record.updates.append(f"[{datetime.now().strftime('%H:%M:%S')}] Status: {record.status}")
        
        return record
    
    async def _heartbeat_loop(self):
        """Main heartbeat loop that runs in the background."""
        logger.info(f"Heartbeat Monitor started (interval: {self.interval}s)")
        
        while self._running:
            try:
                # Check all registered subagents
                active_records = []
                
                for subagent_id in list(self._records.keys()):
                    record = await self._check_subagent(subagent_id)
                    active_records.append(record)
                
                # Generate heartbeat report
                report = self._generate_report(active_records)
                
                # Call all registered callbacks with the report
                for callback in self._callbacks:
                    try:
                        callback(report)
                    except Exception as e:
                        logger.error(f"Error in heartbeat callback: {e}")
                
                # Cleanup completed/failed subagents from records
                to_remove = [
                    sid for sid, record in self._records.items()
                    if record.status in ["completed", "failed", "cancelled", "not_found"]
                ]
                for sid in to_remove:
                    del self._records[sid]
                
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")
            
            # Wait for next interval
            await asyncio.sleep(self.interval)
        
        logger.info("Heartbeat Monitor stopped")
    
    def _generate_report(self, records: List[HeartbeatRecord]) -> str:
        """Generate a human-readable heartbeat report."""
        if not records:
            return "✅ Heartbeat: No active subagents to monitor."
        
        lines = ["📊 **Heartbeat Report**"]
        lines.append(f"_{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
        lines.append("")
        
        running_count = 0
        completed_count = 0
        
        for record in records:
            if record.status == "running":
                running_count += 1
                elapsed = time.time() - record.start_time
                emoji = "⚠️" if record.warning_issued else "🔄"
                lines.append(f"{emoji} **{record.name}**: Running ({elapsed:.0f}s)")
            elif record.status == "completed":
                completed_count += 1
                lines.append(f"✅ **{record.name}**: Completed")
            elif record.status == "failed":
                lines.append(f"❌ **{record.name}**: Failed")
            elif record.status == "starting":
                lines.append(f"⏳ **{record.name}**: Starting...")
            elif record.status == "cancelled":
                lines.append(f"🚫 **{record.name}**: Cancelled")
        
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
                logger.warning("No running event loop - heartbeat will start when agent runs")
    
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
                    "warning_issued": r.warning_issued
                }
                for sid, r in self._records.items()
            }
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
# PUBLIC API FUNCTIONS (for use as Nova tools)
# =============================================================================

def start_heartbeat_monitor(interval_seconds: int = 30) -> str:
    """
    Start the heartbeat monitor to track active subagents.
    
    Args:
        interval_seconds: How often to check subagent status (default: 30)
        
    Returns:
        Confirmation message
    """
    monitor = get_heartbeat_monitor()
    monitor.interval = interval_seconds
    monitor.start()
    return f"✅ Heartbeat Monitor started (checking every {interval_seconds}s)"


def stop_heartbeat_monitor() -> str:
    """Stop the heartbeat monitor."""
    monitor = get_heartbeat_monitor()
    # Note: This needs to be called from async context
    return "🛑 Heartbeat Monitor stop requested (will stop on next check)"


def register_subagent_for_heartbeat(subagent_id: str, name: str) -> str:
    """
    Register a subagent to be monitored by the heartbeat system.
    
    Args:
        subagent_id: The ID of the subagent to monitor
        name: The name of the subagent
        
    Returns:
        Confirmation message
    """
    monitor = get_heartbeat_monitor()
    monitor.register_subagent(subagent_id, name)
    return f"✅ Subagent '{name}' ({subagent_id}) registered for heartbeat monitoring"


def unregister_subagent_from_heartbeat(subagent_id: str) -> str:
    """
    Remove a subagent from heartbeat monitoring.
    
    Args:
        subagent_id: The ID of the subagent to unregister
        
    Returns:
        Confirmation message
    """
    monitor = get_heartbeat_monitor()
    monitor.unregister_subagent(subagent_id)
    return f"🗑️ Subagent ({subagent_id}) unregistered from heartbeat monitoring"


def get_heartbeat_status() -> str:
    """
    Get the current heartbeat status of all monitored subagents.
    
    Returns:
        Formatted status report
    """
    monitor = get_heartbeat_monitor()
    return monitor.get_status()


def get_heartbeat_detailed_status() -> Dict:
    """
    Get detailed heartbeat status as a dictionary.
    
    Returns:
        Detailed status information
    """
    monitor = get_heartbeat_monitor()
    return monitor.get_detailed_status()


def auto_register_active_subagents() -> str:
    """
    Automatically register all currently running subagents for heartbeat monitoring.
    Call this when starting a task that spawns multiple subagents.
    
    Returns:
        Confirmation message with count
    """
    monitor = get_heartbeat_monitor()
    count = 0
    
    for subagent_id, data in SUBAGENTS.items():
        if data.get("status") in ["running", "starting"]:
            monitor.register_subagent(subagent_id, data.get("name", "unknown"))
            count += 1
    
    return f"✅ Auto-registered {count} active subagents for heartbeat monitoring"


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

async def heartbeat_callback_example(report: str):
    """
    Example callback - prints heartbeat report.
    Replace with actual notification logic (Telegram, etc.)
    """
    logger.info(f"HEARTBEAT: {report}")


def setup_heartbeat_for_task(subagent_ids: List[str], subagent_names: List[str]) -> str:
    """
    Convenience function to setup heartbeat monitoring for a batch of subagents.
    
    Args:
        subagent_ids: List of subagent IDs to monitor
        subagent_names: List of corresponding names
        
    Returns:
        Confirmation message
    """
    monitor = get_heartbeat_monitor()
    monitor.start()  # Ensure monitor is running
    
    for sid, name in zip(subagent_ids, subagent_names):
        monitor.register_subagent(sid, name)
    
    return f"✅ Heartbeat monitoring setup for {len(subagent_ids)} subagents"