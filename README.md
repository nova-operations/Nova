<p align="center">
  <img src="Nova.png" alt="Nova Agent Logo" width="200"/>
</p>

<h1 align="center">Nova Agent</h1>

<p align="center">
  <strong>An advanced, self-improving, and persistent AI agent</strong>
</p>

<p align="center">
  <a href="https://railway.app/new/template?template=https://github.com/nova-operations/Nova"><img src="https://railway.app/button.svg" alt="Deploy on Railway"></a>
  <a href="https://github.com/nova-operations/Nova/stargazers"><img src="https://img.shields.io/github/stars/nova-operations/Nova?style=flat-square" alt="GitHub stars"></a>
  <a href="https://github.com/nova-operations/Nova/network/members"><img src="https://img.shields.io/github/forks/nova-operations/Nova?style=flat-square" alt="GitHub forks"></a>
  <a href="https://github.com/nova-operations/Nova/issues"><img src="https://img.shields.io/github/issues/nova-operations/Nova?style=flat-square" alt="GitHub issues"></a>
</p>

---

## 🚀 What is Nova?

Nova is an advanced, autonomous AI agent designed for continuous operation and self-improvement. Built to be deployed efficiently as a worker process (like on Railway), Nova communicates directly with you via a Telegram Bot interface, offering seamless and fast interactions. 

Unlike standard conversational bots, Nova possesses **Persistent Memory**, **Shell Access**, **Model Context Protocol (MCP)** integrations, and the unique ability to **rewrite its own source code** and push these changes up to GitHub, learning and adapting to your specific needs over time.

## ✨ Features

- 🧠 **Self-Improvement**: Nova has full codebase context and GitHub tool integrations. It can iterate on its own code, write tests, push to its repository, and initiate redeployments.
- 💾 **Persistent Memory**: Implements a powerful SQLite / PostgreSQL database to remember user preferences, conversation history, and project context across restarts.
- 🐚 **Shell & Environment Access**: Capable of executing shell commands to interact directly with the environment or local filesystem.
- 🔌 **MCP Tool Capabilities**: Hooks into various MCP servers like `tavily` for web search, handling APIs on the fly.
- 💬 **Telegram Interface**: Access control and interact with Nova via a clean, native Telegram UI—bringing an AI software engineer straight to your pocket.

## 🛠 Prerequisites

- Python 3.10+
- An [OpenRouter](https://openrouter.ai/) API Key (for LLM capabilities)
- A [Telegram Bot Token](https://core.telegram.org/bots#how-do-i-create-a-bot) from `@BotFather`
- A GitHub Personal Access Token (with `repo` permissions, for self-improvement)

## 📦 Installation & Quick Start

The best way to develop Nova is to run it in a virtual environment.

### 1. Fork & Clone
Fork the repository to your own GitHub account so Nova can push updates to itself.
```bash
git clone https://github.com/<your-username>/Nova.git
cd Nova
```

### 2. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment
Copy the example environment file:
```bash
cp .env.example .env
```
Fill in the `.env` file with your credentials:
- `TELEGRAM_BOT_TOKEN="your_token"`
- `OPENROUTER_API_KEY="your_api_key"`
- `GITHUB_TOKEN="your_github_token"`
- `GITHUB_REPO="<your-username>/Nova"`

### 4. Run the Agent
```bash
python -m nova.agent
# Alternatively, use the start script:
./start.sh
```
Go to your Telegram bot and say `/start` to begin interacting!

## 🚢 Deployment

We strongly recommend deploying Nova on [Railway](https://railway.app/). Nova works brilliantly out of the box with Railway's PostgreSQL databases for managed volume persistence.

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/nova-operations/Nova)

For detailed installation options see our [Railway Setup Guide](RAILWAY_SETUP.md).

## 🤝 Contributing

We welcome contributions from everyone! Whether you want to fix a bug, add a cool new tool, or optimize prompt contexts, please do!

Please read our [Contributing Guidelines](CONTRIBUTING.md) to understand the workflow.

### Code Organization
- `nova/tools/`: Where all actionable capabilities branch out (audio, github, agent orchestration, system, web, etc.).
- `nova/agent.py`: The entrypoint of the orchestrator.
- `nova/telegram_bot.py`: Handles all Telegram callbacks, formatting, and messaging.

## 👥 Contributors

Thanks goes to these wonderful people for contributing to Nova! You can be the next—submit a PR!

<a href="https://github.com/nova-operations/Nova/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=nova-operations/Nova" />
</a>

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
