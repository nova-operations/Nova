# Nova Scheduler Documentation

The Nova Scheduler is a persistent, DB-backed cron task system that supports:
- **Standalone Shell Scripts**: Execute any shell script on a schedule
- **Subagent Recalls**: Trigger AI subagents at scheduled times
- **Silent Tasks**: Background tasks without notifications

## Quick Start

### 1. Initialize the Scheduler

The scheduler automatically starts when you run `agent.py` or `telegram_bot.py`. It initializes from the database and loads all active scheduled tasks.

```python
# In agent.py or telegram_bot.py
from nova.tools.scheduler import initialize_scheduler

# Initialize on startup
initialize_scheduler()
```

### 2. Add a Scheduled Task

Use the `add_scheduled_task` function to create new scheduled tasks:

#### Example: Standalone Shell Script
```python
add_scheduled_task(
    task_name="daily_backup",
    schedule="0 2 * * *",  # Daily at 2 AM
    task_type="standalone_sh",
    script_path="/app/scripts/backup.sh",
    notification_enabled=True
)
```

#### Example: Subagent Recall
```python
add_scheduled_task(
    task_name="daily_report",
    schedule="0 9 * * 1-5",  # Weekdays at 9 AM
    task_type="subagent_recall",
    subagent_name="Reporter",
    subagent_instructions="You are a data analyst that generates reports.",
    subagent_task="Generate a summary of yesterday's system metrics and send to the team.",
    notification_enabled=True
)
```

#### Example: Silent Task
```python
add_scheduled_task(
    task_name="cleanup_temp",
    schedule="*/30 * * * *",  # Every 30 minutes
    task_type="silent"
)
```

## Cron Expressions

The scheduler uses standard cron format. Common examples:

| Expression | Description |
|------------|-------------|
| `* * * * *` | Every minute |
| `*/5 * * * *` | Every 5 minutes |
| `0 * * * *` | Every hour |
| `0 0 * * *` | Daily at midnight |
| `0 2 * * *` | Daily at 2 AM |
| `0 9 * * 1-5` | Weekdays at 9 AM |
| `0 */6 * * *` | Every 6 hours |
| `0 0 * * 0` | Weekly on Sunday |

### Cron Format
```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6) (Sunday=0)
│ │ │ │ │
* * * * *
```

## Available Tools

Once the agent is running, you can manage tasks using these functions:

| Function | Description |
|----------|-------------|
| `add_scheduled_task(...)` | Create a new scheduled task |
| `list_scheduled_tasks()` | List all scheduled tasks |
| `get_scheduled_task(task_name)` | Get details of a specific task |
| `update_scheduled_task(task_name, ...)` | Update an existing task |
| `remove_scheduled_task(task_name)` | Delete a scheduled task |
| `pause_scheduled_task(task_name)` | Pause a task (won't run) |
| `resume_scheduled_task(task_name)` | Resume a paused task |
| `run_scheduled_task_now(task_name)` | Trigger a task immediately |
| `get_scheduler_status()` | Check scheduler health |

## Environment Variables

Ensure these variables are set in your environment:

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | For notifications |
| `TELEGRAM_CHAT_ID` | Chat ID for notifications | For notifications |

## Database Schema

### `scheduled_tasks` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `task_name` | String | Unique task identifier |
| `schedule` | String | Cron expression |
| `task_type` | Enum | `standalone_sh`, `subagent_recall`, `silent` |
| `script_path` | Text | Path to shell script (for standalone_sh) |
| `subagent_name` | String | Subagent name (for subagent_recall) |
| `subagent_instructions` | Text | System instructions (for subagent_recall) |
| `subagent_task` | Text | Task prompt (for subagent_recall) |
| `status` | Enum | `active` or `paused` |
| `notification_enabled` | Boolean | Whether to send Telegram notifications |
| `last_run` | DateTime | Last execution timestamp |
| `last_status` | String | Last execution status |
| `last_output` | Text | Last execution output |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

### `apscheduler_jobs` Table

Internal table used by APScheduler to track job state.

## Telegram Notifications

When `notification_enabled=True`, the scheduler will send Telegram notifications for:

- **Task Failures**: When a task fails to execute
- **Subagent Triggers**: When a subagent recall task is triggered

Make sure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in your environment.

## Running the Migration

Before using the scheduler for the first time, run the migration script:

```bash
python migrations/scheduler_tables.py
```

This creates the necessary tables in your PostgreSQL database.

## Examples

### Example 1: Hourly Health Check

```python
add_scheduled_task(
    task_name="health_check",
    schedule="0 * * * *",
    task_type="standalone_sh",
    script_path="/app/scripts/health_check.sh",
    notification_enabled=True
)
```

### Example 2: Weekly Report Generation

```python
add_scheduled_task(
    task_name="weekly_summary",
    schedule="0 8 * * 0",
    task_type="subagent_recall",
    subagent_name="WeeklyReporter",
    subagent_instructions="You are a professional report writer.",
    subagent_task="Generate a weekly summary of all completed tasks from the logs.",
    notification_enabled=True
)
```

### Example 3: Data Sync (Silent)

```python
add_scheduled_task(
    task_name="sync_data",
    schedule="*/15 * * * *",
    task_type="silent"
)
```

## Troubleshooting

### Scheduler not starting

Check that:
1. `DATABASE_URL` is set correctly
2. PostgreSQL database is accessible
3. The migration has been run

### Tasks not executing

1. Verify the task status is `active`
2. Check the cron expression is valid
3. Review logs for execution errors
4. Ensure `last_output` doesn't contain errors

### Notifications not sending

1. Check `TELEGRAM_BOT_TOKEN` is set
2. Verify `TELEGRAM_CHAT_ID` is correct
3. Ensure `notification_enabled=True` for the task