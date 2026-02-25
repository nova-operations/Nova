---
name: git-workflow
description: Standardized workflow for managing the Nova Git repository, commits, and pushes.
---

# Git Workflow Skill

Use this skill to ensure high-quality Git hygiene when modifying the Nova codebase.

## When to Use

- Before committing changes to the repository.
- When cleaning up branches or managing remotes.
- When you need to verify the state of the Git repository.

## Workflow

1. **Check Status**: Always run `git status` to see what files are modified.
2. **Atomic Commits**: Group related changes together. Don't mix feature additions with bug fixes in one commit.
3. **Verify Build**: Before pushing, ensure the agent still initializes correctly (`python smoke_test.py`).
4. **Pull First**: Always run `pull_latest_changes` to ensure you aren't overwriting someone else's work (or your own previous push).
5. **Detailed Messages**: Use descriptive, imperative commit messages (e.g., "Add skill system" instead of "Fixed stuff").

## Scripts

### `git_health.sh`
Check if the local repo is in a good state and matches the remote main branch.

## References

- `references/commit-messages.md`: Guide on writing great commit messages.
- `references/safety-checks.md`: Checklist before pushing to production (Railway).
