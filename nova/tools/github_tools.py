import os
import subprocess
import logging
from typing import Optional, List


def push_to_github(
    commit_message: str, branch: str = "main", files: Optional[List[str]] = None
) -> str:
    """
    Commits and pushes changes to the GitHub repository.
    This triggers a redeployment on Railway if connected.

    Args:
        commit_message: The commit message describing the changes.
        branch: The branch to push to (default: main).
        files: Optional list of files to add. If None, adds all changes.

    Returns:
        A status message indicating success or failure.
    """
    # Prioritize the persistent repo path
    repo_dir = "/app/data/nova_repo"
    if not os.path.exists(repo_dir):
        repo_dir = os.getcwd()  # Fallback

    try:
        # Check if directory exists and is a git repo
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            return f"Error: {repo_dir} is not a Git repository. Agent cannot push code."

        # Configure git identity
        subprocess.run(
            ["git", "config", "user.email", "nova@agent.ai"], cwd=repo_dir, check=False
        )
        subprocess.run(
            ["git", "config", "user.name", "Nova Agent"], cwd=repo_dir, check=False
        )

        # Add files
        if files:
            for file in files:
                subprocess.run(["git", "add", file], cwd=repo_dir, check=True)
        else:
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)

        # Commit
        subprocess.run(
            ["git", "commit", "-m", commit_message], cwd=repo_dir, check=True
        )

        # Push
        github_token = os.getenv("GITHUB_TOKEN")
        github_repo = os.getenv("GITHUB_REPO")

        if github_token and github_repo:
            remote_url = f"https://{github_token}@github.com/{github_repo}.git"
            subprocess.run(
                ["git", "remote", "set-url", "origin", remote_url],
                cwd=repo_dir,
                check=True,
            )

        result = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return f"Successfully pushed changes to {branch}. Deployment should start shortly."
        else:
            return f"Error pushing to GitHub: {result.stderr}"

    except subprocess.CalledProcessError as e:
        return f"Git command failed: {e}"
    except Exception as e:
        return f"Error executing git operations: {e}"


def pull_latest_changes(branch: str = "main") -> str:
    """
    Pulls the latest changes from the remote repository.
    Uses git reset --hard to ensures the local repo exactly matches the remote.
    """
    repo_dir = "/app/data/nova_repo"
    if not os.path.exists(repo_dir):
        repo_dir = os.getcwd()

    try:
        # 1. Fetch
        subprocess.run(["git", "fetch", "origin", branch], cwd=repo_dir, check=True)

        # 2. Reset hard
        result = subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return f"Successfully updated to latest {branch} (forced update)."
        else:
            return f"Error resetting to remote: {result.stderr}"
    except Exception as e:
        return f"Error pulling changes: {e}"
