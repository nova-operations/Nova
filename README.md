# Nova Agent

Nova is an advanced, self-improving AI agent designed to run on Railway.
It uses OpenRouter for its LLM capabilities and communicates via Telegram.

## Features
- **Self-Improvement**: Nova can modify its own codebase to enhance its capabilities.
- **Persistent Memory**: Uses SQLite for storing conversation history and learned information.
- **Shell Access**: Can execute shell commands to interact with the environment.
- **Telegram Integration**: Provides a user-friendly interface via Telegram.

## Setup

1.  Clone the repository.
2.  Install dependencies: `pip install -r requirements.txt`
3.  Set up environment variables in a `.env` file:
    - `OPENROUTER_API_KEY`: Your OpenRouter API key.
    - `TELEGRAM_BOT_TOKEN`: Your Telegram Bot token.
4.  Run the agent: `python -m nova.agent` (or `python start.sh`)

## Deployment on Railway

Nova is optimized for [Railway](https://railway.app). It can run as a persistent worker using either a managed PostgreSQL database or a Docker volume.

### 1. Simple Deployment (PostgreSQL)

This is the recommended setup for robust performance and memory persistence.

1.  **Initialize Project:**
    ```bash
    railway init
    ```
2.  **Add PostgreSQL:**
    *   In the Railway Dashboard, add a **PostgreSQL** service. Nova will automatically detect the `DATABASE_URL`.
3.  **Configure Variables:**
    *   Add your environment variables in the Railway **Variables** tab:
        - `TELEGRAM_BOT_TOKEN`: From [@BotFather](https://t.me/botfather).
        - `OPENROUTER_API_KEY`: From OpenRouter.
        - `GITHUB_TOKEN`: Required for self-improvement (pushing code).
        - `GITHUB_REPO`: Your repository path (e.g., `Morty-pilot/Nova`).
4.  **Deploy:**
    ```bash
    railway up
    ```

### 2. Manual Setup (Persistent Volume)

If you prefer using SQLite, you MUST mount a volume to `/app/data` to prevent data loss:

1.  Go to your Nova service in Railway.
2.  Navigate to **Settings** -> **Volumes** -> **Add Volume**.
3.  Set the **Mount Path** to `/app/data`.

### 3. Automation Script

You can also use the included setup script to automate the process:
```bash
./railway_setup.sh
```

## Self-Improvement Workflow

Nova can push changes back to its own repository to improve its functionality.
- Ensure the `GITHUB_TOKEN` has `repo` permissions.
- Railway will automatically trigger a redeploy whenever Nova pushes a change to the `main` branch.

For more details, see [RAILWAY_SETUP.md](./RAILWAY_SETUP.md).
