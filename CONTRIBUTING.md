# Contributing to Nova

First off, thank you for considering contributing to Nova! It's people like you that make Nova such a self-improving and robust AI agent.

## How to Contribute

1. **Fork the repository** on GitHub.
2. **Clone your fork** locally: `git clone https://github.com/<your-username>/Nova.git`
3. **Set up the virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Create a branch** for your feature or bug fix: `git checkout -b feature/your-feature-name`
5. **Make your changes**. If you are creating a new tool for Nova, please place it in the appropriate `nova/tools/<tool-category>/` subdirectory and update any necessary agent configurations to register the tool. Add a README or extend the existing folder README.
6. **Test your code**: Run `pytest tests/` to make sure your changes do not break existing logic. Make sure to adhere to existing style guidelines.
7. **Commit and Push**:
   ```bash
   git commit -m "feat: add your feature description"
   git push origin feature/your-feature-name
   ```
8. **Submit a Pull Request** via GitHub. Detailed descriptions of what your PR introduces will help us review it faster!

## Code Architecture Hints
- **Registries**: When creating new tools, typically they need to be registered in `nova.tools.core.registry` or similar agent builders inside `nova/agent.py`.
- **Tools**: Try to keep tools atomic and focused on one specific job (e.g., FileSystem manipulation, Database wiping, etc.). Add README details if making a new tool category.
- **Testing**: Add mock tests in `tests/` whenever you introduce new capabilities. If you break the `tests/test_bot_handlers.py` logic, fix the tests simultaneously!

We appreciate all your efforts and look forward to growing Nova together!
