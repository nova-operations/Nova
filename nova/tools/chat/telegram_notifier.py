"""
Telegram notification helper for deployment and system alerts.
"""
import os
import logging
import subprocess

logger = logging.getLogger(__name__)


def get_telegram_bot_token() -> str:
    """Get Telegram bot token from environment."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set - notifications disabled")
        return None
    return token


def get_notifications_chat_id() -> str:
    """Get the chat ID for system notifications (falls back to TELEGRAM_USER_WHITELIST)."""
    # Use dedicated notifications channel if set, otherwise use whitelist
    chat_id = os.getenv("TELEGRAM_NOTIFICATIONS_CHAT_ID")
    if not chat_id:
        # Fallback to first user in whitelist
        whitelist = os.getenv("TELEGRAM_USER_WHITELIST", "")
        if whitelist:
            chat_id = whitelist.split(",")[0].strip()
    return chat_id


def send_telegram_message(chat_id: str, message: str) -> bool:
    """
    Send a message via Telegram bot using direct API call.

    Returns:
        True if successful, False otherwise.
    """
    token = get_telegram_bot_token()
    if not token:
        logger.warning("Cannot send notification - no bot token")
        return False

    if not chat_id:
        logger.warning("Cannot send notification - no chat_id")
        return False

    import requests

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"Notification sent to {chat_id}")
            return True
        else:
            logger.error(f"Failed to send notification: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        return False


def notify_deployment_initiated(commit_message: str) -> bool:
    """
    Send deployment notification to Telegram.
    Format: "DEPLOYMENT INITIATED: [Commit Message]"
    """
    chat_id = get_notifications_chat_id()
    if not chat_id:
        logger.warning("No notification chat ID configured")
        return False

    message = f"DEPLOYMENT INITIATED: {commit_message}"
    return send_telegram_message(chat_id, message)


def notify_system_online() -> bool:
    """
    Send system online notification with latest git commit.
    Format: "System Online. Latest Updates: [Latest Git Commit Message]"
    """
    chat_id = get_notifications_chat_id()
    if not chat_id:
        logger.warning("No notification chat ID configured")
        return False

    # Get latest commit message
    commit_msg = get_latest_commit_message()

    message = f"System Online. Latest Updates: {commit_msg}"
    return send_telegram_message(chat_id, message)


def get_latest_commit_message() -> str:
    """
    Get the latest commit message from git.
    """
    repo_dir = "/app/data/nova_repo"
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        repo_dir = os.getcwd()

    try:
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%s"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logger.warning(f"Could not get latest commit: {e}")

    return "No recent updates"
