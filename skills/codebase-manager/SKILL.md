---
name: codebase-manager
description: Tools and instructions for managing and improving the Nova codebase.
---

# Codebase Manager Skill

This skill helps you maintain and improve your own codebase reliably.

## Instructions

1. **Safety First**: Before making significant changes, always run `python tests/smoke_test.py` to ensure you haven't broken core functionality.
2. **Contextual Awareness**: Your primary workspace is `/app/data/nova_repo`. All tool modifications should happen relative to this path.
3. **Atomic Changes**: Commit small, logical changes rather than large dumps. Use descriptive commit messages.
4. **Tool Creation**: When creating new tools:
    - Place them in `nova/tools/`.
    - Register them in `nova/agent.py` in the `get_agent` function.
    - Add any new dependencies to `requirements.txt`.
5. **Skill Creation**: If you find a set of operations that are reusable, create a new Skill instead of a single tool.

## Scripts

### `verify_repo.py`
Located in `scripts/verify_repo.py`. Use this to check for common linting issues or directory structure violations.

## References

- `guides/tool-pattern.md`: A reference for building Agno-compatible tools.
