# Railway Setup Guide for Nova

This guide explains how to set up Nova on Railway from scratch. Nova can use either a persistent Volume (for SQLite) or a managed PostgreSQL database for its memory.

## Option A: Using PostgreSQL (Recommended)

PostgreSQL is more robust for session management and persistent memory.

1. **Create a Railway Project:**
   ```bash
   railway init
   ```
2. **Add PostgreSQL Service:**
   * In the Railway Dashboard, click **+ Add Service** -> **Database** -> **PostgreSQL**.
   * This will automatically add a `DATABASE_URL` environment variable to your project.
3. **Configure Environment Variables:**
   * Go to your Nova service's **Variables** tab.
   * Add the following from your `.env`:
     * `TELEGRAM_BOT_TOKEN`
     * `OPENROUTER_API_KEY`
     * `GITHUB_TOKEN`
     * `GITHUB_REPO`
4. **Deploy:**
   ```bash
   railway up
   ```

---

## Option B: Using SQLite with Persistent Volume

If you prefer SQLite, you must mount a volume to prevent data loss on redeploys.

1. **Create a Railway Project:**
   ```bash
   railway init
   ```
2. **Add a Volume:**
   * In the Railway Dashboard, go to your Nova service.
   * Click **Settings** -> **Volumes** -> **+ Add Volume**.
   * Set the **Mount Path** to `/app/data`.
3. **Configure Environment Variables:**
   * Add `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, etc.
4. **Deploy:**
   ```bash
   railway up
   ```

---

## Deployment Configuration (`railway.json`)

Your `railway.json` is already configured to use the `Dockerfile`. Since this is a Telegram Bot (Worker), it does not listen on a port. Railway handles this automatically if you don't define a health check that requires a port.

## Self-Improvement (GitHub Sync)

To allow Nova to push changes back to your repository:
1. Ensure `GITHUB_TOKEN` has `repo` permissions.
2. Ensure `GITHUB_REPO` is set to `yourusername/reponame`.
3. Railway will automatically redeploy when Nova pushes a change to the `main` branch.
