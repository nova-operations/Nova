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

## Deployment

Deploy directly to Railway using the provided `Dockerfile` or `railway.json`.
