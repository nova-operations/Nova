# Nova Database Migrations

This directory contains scripts for managing the Nova database schema.

## How to use

To initialize or update the database schema, run:

```bash
python migrations/migrate.py
```

This script handles:
1. Creating all necessary tables for MCP, Specialists, and Scheduler.
2. Migrating legacy `mcp_servers` data to the new `nova_mcp_servers` table.
3. Adding missing columns (like `team_members` in `scheduled_tasks`).

## Structure

- `001_initial_schema.py`: The main migration script (aliased as `migrate.py`).
- All models are imported from the `nova` package to ensure the `Base` metadata is complete.

## Fallback Logic

The migration script respects the `DATABASE_URL` environment variable. If not set, it defaults to a local SQLite database at `data/nova_memory.db`.
