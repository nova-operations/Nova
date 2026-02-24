import os
import subprocess
import logging
from typing import Optional, List

def push_to_github(commit_message: str, branch: str = "main", files: Optional[List[str]] = None) -> str:
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
    try:
        # Check if git is configured
        # We need to set user email and name if not set
        subprocess.run(["git", "config", "--global", "user.email", "nova@agent.ai"], check=False)
        subprocess.run(["git", "config", "--global", "user.name", "Nova Agent"], check=False)
        
        # Add files
        if files:
            for file in files:
                subprocess.run(["git", "add", file], check=True)
        else:
            subprocess.run(["git", "add", "."], check=True)
            
        # Commit
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        
        # Push
        # We assume the remote 'origin' is set up with the correct authentication token
        # Or we can construct the URL with the token if provided in env
        github_token = os.getenv("GITHUB_TOKEN")
        github_repo = os.getenv("GITHUB_REPO") # e.g. "username/repo"
        
        if github_token and github_repo:
            remote_url = f"https://{github_token}@github.com/{github_repo}.git"
            # Update remote URL to include token
            subprocess.run(["git", "remote", "set-url", "origin", remote_url], check=True)
            
        result = subprocess.run(["git", "push", "origin", branch], capture_output=True, text=True)
        
        if result.returncode == 0:
            return f"Successfully pushed changes to {branch}. Deployment should start shortly."
        else:
            return f"Error pushing to GitHub: {result.stderr}"
            
    except subprocess.CalledProcessError as e:
        return f"Git command failed: {e}"
    except Exception as e:
        return f"Error executing git operations: {e}"

def pull_latest_changes(branch: str = "main") -> str:
    """Pulls the latest changes from the remote repository."""
    try:
        result = subprocess.run(["git", "pull", "origin", branch], capture_output=True, text=True)
        if result.returncode == 0:
            return f"Successfully pulled latest changes from {branch}."
        else:
            return f"Error pulling changes: {result.stderr}"
    except Exception as e:
        return f"Error pulling changes: {e}"
