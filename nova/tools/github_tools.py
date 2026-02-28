import os
import subprocess
import logging
import sys
import json
from typing import Optional, List, Tuple

from nova.tools.project_manager import get_active_project


def get_repo_dir(project_name: Optional[str] = None) -> str:
    """
    Returns the absolute path of the repository.
    Prioritizes the active project from the database, then falls back to defaults.
    """
    try:
        active_json = get_active_project()
        active_data = json.loads(active_json)

        if active_data.get("status") == "success":
            # If a specific project was requested, we could look it up here.
            # For now, we return the active project's path.
            return active_data.get("absolute_path")
    except Exception as e:
        logging.warning(f"Failed to get active project: {e}")

    repo_dir = "/app/data/nova_repo"
    if not os.path.exists(repo_dir):
        repo_dir = os.getcwd()
    return repo_dir


def check_active_tasks() -> Tuple[bool, str]:
    """
    Check if there are active tasks running.
    Returns (has_active_tasks, message).
    """
    try:
        from nova.db.engine import get_session_factory
        from nova.db.deployment_models import ActiveTask, TaskStatus

        session_factory = get_session_factory()
        session = session_factory()

        try:
            active_count = (
                session.query(ActiveTask)
                .filter(ActiveTask.status == TaskStatus.RUNNING)
                .count()
            )

            if active_count > 0:
                # Get details of active tasks
                active_tasks = (
                    session.query(ActiveTask)
                    .filter(ActiveTask.status == TaskStatus.RUNNING)
                    .all()
                )

                task_details = ", ".join(
                    [f"{t.subagent_name}({t.task_id[:8]}...)" for t in active_tasks[:3]]
                )

                return True, f"{active_count} active task(s) running: {task_details}"

            return False, "No active tasks"

        finally:
            session.close()

    except Exception as e:
        logging.warning(f"Could not check active tasks: {e}")
        return False, f"Could not verify task status: {e}"


def set_deployment_pending_flag(task_id: str, pending: bool = True) -> bool:
    """
    Set or clear the deployment_pending flag on a task.
    This prevents deployments while a task is in a critical section.
    """
    try:
        from nova.db.engine import get_session_factory
        from nova.db.deployment_models import ActiveTask
        import json

        session_factory = get_session_factory()
        session = session_factory()

        try:
            task = (
                session.query(ActiveTask).filter(ActiveTask.task_id == task_id).first()
            )

            if task:
                state = json.loads(task.current_state) if task.current_state else {}
                state["deployment_pending"] = pending
                task.current_state = json.dumps(state)
                session.commit()
                return True

            return False

        finally:
            session.close()

    except Exception as e:
        logging.warning(f"Could not set deployment flag: {e}")
        return False


def push_to_github(
    commit_message: str,
    branch: str = "main",
    files: Optional[List[str]] = None,
    force: bool = False,
    skip_tests: bool = False,
) -> str:
    """
    Commits and pushes changes to the GitHub repository.
    This triggers a redeployment on Railway if connected.

    Before pushing:
    1. Checks for active tasks (unless force=True).
    2. Runs the test suite (unless skip_tests=True).

    Use force=True to skip the active task check.
    Use skip_tests=True if you are absolutely sure about the changes.

    Args:
        commit_message: The commit message describing the changes.
        branch: The branch to push to (default: main).
        files: Optional list of files to add. If None, adds all changes.
        force: If True, skip active task check (default: False).
        skip_tests: If True, skip running tests before push (default: False).

    Returns:
        A status message indicating success or failure.
    """
    repo_dir = get_repo_dir()

    # 1. Run tests unless skipped
    if not skip_tests:
        logging.info("Running tests before push...")
        test_env = os.environ.copy()
        test_env["PYTHONPATH"] = repo_dir

        try:
            # Run pytest from the repo directory
            test_result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/"],
                cwd=repo_dir,
                env=test_env,
                capture_output=True,
                text=True,
                check=False,
            )

            if test_result.returncode != 0:
                return (
                    f"❌ PUSH REJECTED: Tests failed!\n\n"
                    f"You must fix tests before pushing to ensure system stability.\n"
                    f"Use skip_tests=True only for emergency hotfixes when tests are irrelevant.\n\n"
                    f"Output:\n{test_result.stdout[-1000:]}\n{test_result.stderr[-1000:]}"
                )
            logging.info("✅ Tests passed.")
        except Exception as e:
            return f"Error running tests: {e}"

    # 2. Check for active tasks unless force is True
    if not force:
        has_active, task_message = check_active_tasks()

        if has_active:
            # Set deployment_pending flag on all active tasks
            try:
                from nova.db.engine import get_session_factory
                from nova.db.deployment_models import ActiveTask, TaskStatus

                session_factory = get_session_factory()
                session = session_factory()

                try:
                    active_tasks = (
                        session.query(ActiveTask)
                        .filter(ActiveTask.status == TaskStatus.RUNNING)
                        .all()
                    )

                    for task in active_tasks:
                        set_deployment_pending_flag(task.task_id, True)

                finally:
                    session.close()
            except Exception as e:
                logging.warning(f"Could not set deployment flags: {e}")

            # Return message indicating tasks are running
            return (
                f"Cannot push: {task_message}. "
                f"Either wait for tasks to complete or use force=True to override. "
                f"Deployment will be blocked until all tasks finish."
            )

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
            # Clear deployment_pending flags after successful push
            if not force:
                try:
                    from nova.db.engine import get_session_factory
                    from nova.db.deployment_models import ActiveTask, TaskStatus

                    session_factory = get_session_factory()
                    session = session_factory()

                    try:
                        active_tasks = (
                            session.query(ActiveTask)
                            .filter(ActiveTask.status == TaskStatus.RUNNING)
                            .all()
                        )

                        for task in active_tasks:
                            set_deployment_pending_flag(task.task_id, False)

                    finally:
                        session.close()
                except Exception as e:
                    logging.warning(f"Could not clear deployment flags: {e}")

            # Send Telegram notification for deployment initiation
            try:
                from nova.tools.telegram_notifier import notify_deployment_initiated

                notify_deployment_initiated(commit_message)
            except Exception as notif_err:
                logging.warning(f"Failed to send deployment notification: {notif_err}")

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
    repo_dir = get_repo_dir()

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


def get_git_status(project_name: Optional[str] = None) -> str:
    """
    Returns a comprehensive summary of the git repository status.
    Includes current branch, uncommitted changes, and the last 5 commits.
    """
    repo_dir = get_repo_dir(project_name)

    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return f"Error: {repo_dir} is not a Git repository."

    try:
        # Get status
        status_res = subprocess.run(
            ["git", "status", "-sb"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        status_output = status_res.stdout.strip()

        # Get recent commits
        log_res = subprocess.run(
            ["git", "log", "-n", "5", "--oneline"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        log_output = log_res.stdout.strip()

        summary = f"📁 **Repository:** `{repo_dir}`\n\n"
        summary += f"🌿 **Current Status:**\n```\n{status_output}\n```\n\n"
        summary += f"📜 **Recent Commits:**\n```\n{log_output}\n```"

        return summary
    except subprocess.CalledProcessError as e:
        return f"Git command failed: {e.stderr}"
    except Exception as e:
        return f"Error retrieving git status: {e}"
