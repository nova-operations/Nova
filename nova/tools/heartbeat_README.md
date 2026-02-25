# Agentic Heartbeat System - Implementation Guide

## Overview

The **Agentic Heartbeat** pattern is an architectural pattern for long-running agentic tasks. It provides periodic feedback from the Project Manager (Nova) to the user during tasks that take significant time to complete.

## Problem Solved

When Nova spawns subagents for complex tasks, users often experience "silent waiting" - not knowing if the agent is still working, failed, or stuck. The heartbeat system solves this by:

1. **Continuous Monitoring**: Background polling of all active subagents
2. **Automatic Registration**: New subagents are automatically enrolled in monitoring
3. **Warning System**: Alerts when tasks run longer than expected
4. **Status Reports**: Human-readable status updates at any time

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         NOVA (PM Agent)                         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │              HeartbeatMonitor (Background Task)          │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │ │
│  │  │ Subagent 1  │  │ Subagent 2  │  │ Subagent N  │       │ │
│  │  │  (Research) │  │  (Coding)  │  │  (Testing)  │       │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘       │ │
│  │       ↑                ↑                ↑                 │ │
│  │       └────────────────┴────────────────┘                 │ │
│  │              Polling every 30 seconds                     │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                 │
│  User gets: "🔄 Research-Agent: Running (45s)"                │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Details

### Files Created/Modified

| File | Purpose |
|------|---------|
| `nova/tools/heartbeat.py` | Core heartbeat monitoring system |
| `nova/tools/heartbeat_integration.py` | Auto-registration helpers |
| `nova/tools/subagent.py` | Modified to auto-register new subagents |
| `nova/agent.py` | Updated to include heartbeat tools |

### Core Components

#### 1. HeartbeatMonitor Class
```python
class HeartbeatMonitor:
    """Background monitor that periodically checks on active subagents."""
    
    def __init__(self, interval: int = 30):
        self.interval = interval          # Check every N seconds
        self._records: Dict[str, HeartbeatRecord] = {}
        self._callbacks: List[callable] = []
    
    def register_subagent(self, subagent_id: str, name: str)
    def unregister_subagent(self, subagent_id: str)
    def start(self)  # Start background monitoring
    async def stop(self)
    def get_status(self) -> str  # Human-readable report
```

#### 2. HeartbeatRecord Dataclass
```python
@dataclass
class HeartbeatRecord:
    subagent_id: str
    name: str
    status: str           # running, completed, failed, etc.
    last_check: float
    start_time: float
    warning_issued: bool  # True if task running >2 min
    updates: List[str]    # Status history
```

### Available Tools (for Nova)

| Tool | Description |
|------|-------------|
| `start_heartbeat_monitor(30)` | Start background monitoring |
| `get_heartbeat_status()` | Get formatted status report |
| `get_heartbeat_detailed_status()` | Get JSON status |
| `register_subagent_for_heartbeat(id, name)` | Manually register |
| `unregister_subagent_from_heartbeat(id)` | Stop monitoring |
| `auto_register_active_subagents()` | Register all running |

## Usage Guide

### Automatic Mode (Recommended)

When Nova creates a subagent, it's **automatically registered**:

```
User: "Analyze this codebase and create a report"

Nova: 
1. Creates "Analysis-Agent" subagent
2. System automatically registers it for heartbeat
3. Returns: "Subagent 'Analysis-Agent' created with ID: xxx"
```

Nova can then periodically check status:
```
Nova calls: get_heartbeat_status()
→ "📊 Heartbeat Report
   🔄 Analysis-Agent: Running (45s)
   Summary: 1 running, 0 completed"
```

### Manual Mode

If you want explicit control:

```python
# Start monitoring with custom interval
start_heartbeat_monitor(interval_seconds=15)

# Register specific subagents
register_subagent_for_heartbeat("abc-123", "My-Agent")

# Get status anytime
status = get_heartbeat_status()
```

### Integration with User Notifications

To send heartbeat updates to users (e.g., via Telegram), register a callback:

```python
from nova.tools.heartbeat import get_heartbeat_monitor, start_heartbeat_monitor

def notify_user(report: str):
    # Send to Telegram, Discord, etc.
    telegram_bot.send_message(user_id, report)

monitor = get_heartbeat_monitor()
monitor.register_callback(notify_user)
start_heartbeat_monitor()
```

## Nova's Operational Workflow

Here's how Nova should use the heartbeat system:

```python
# 1. Analyze request
# 2. Spawn subagent(s)
result = create_subagent(
    name="Research-Agent", 
    instructions="Research the topic...",
    task="Find information about..."
)

# 3. Start heartbeat monitoring
start_heartbeat_monitor(30)

# 4. Periodically provide updates to user
# Call get_heartbeat_status() and include in responses

# 5. When user asks or subagent completes
# Gather final results with get_subagent_result()
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `HEARTBEAT_INTERVAL_SECONDS` | 30 | How often to check subagent status |
| `HEARTBEAT_WARNING_THRESHOLD` | 120 | Seconds before issuing a warning |

## Example Output

### Running Status
```
📊 Heartbeat Report
_2024-01-15 14:30:45_

🔄 **Research-Agent**: Running (45s)
🔄 **Code-Agent**: Running (30s)
🔄 **Data-Agent**: Running (15s)

Summary: 3 running, 0 completed
```

### With Warning
```
📊 Heartbeat Report
_2024-01-15 14:32:15_

⚠️ **Research-Agent**: Running (135s)
🔄 **Code-Agent**: Running (60s)

Summary: 2 running, 0 completed
```

### Completion
```
📊 Heartbeat Report
_2024-01-15 14:33:00_

✅ **Research-Agent**: Completed
🔄 **Code-Agent**: Running (105s)

Summary: 1 running, 1 completed
```

## Testing

Run the built-in test:
```bash
python -m nova.tools.heartbeat_test
```

Or manual test:
```python
import asyncio
from nova.tools.heartbeat import start_heartbeat_monitor, get_heartbeat_status
from nova.tools.subagent import SUBAGENTS

# Create mock subagents
SUBAGENTS["test-1"] = {"name": "Test-Agent", "status": "running"}

# Start monitoring
start_heartbeat_monitor(5)

# Wait and check
asyncio.sleep(6)
print(get_heartbeat_status())
```

## Best Practices

1. **Always start heartbeat** when spawning subagents for user-facing tasks
2. **Include heartbeat status** in responses during long operations
3. **Use warnings** to identify stuck or slow subagents
4. **Clean up** completed subagents from monitoring (automatic)
5. **Provide callbacks** for real-time user notifications

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No heartbeat updates | Call `start_heartbeat_monitor()` first |
| Subagent not showing | Check it's in `SUBAGENTS` dict |
| Monitor not running | Ensure called from async context |
| Import errors | Check `nova/tools/__init__.py` exports |