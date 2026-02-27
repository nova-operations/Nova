#!/bin/bash

# Configuration
REPO_DIR="/app/data/nova_repo"
SKILLS_DIR="/app/data/skills"

# Ensure /app/data exists (it should be our persistent volume)
mkdir -p /app/data
mkdir -p "$SKILLS_DIR"

echo "🚀 Starting Nova environment setup..."

# Function to send startup notification via Python
send_startup_notification() {
    python3 -c "
import os
import sys

# Add the repo to path if it exists
if os.path.exists('$REPO_DIR'):
    sys.path.insert(0, '$REPO_DIR')

try:
    from nova.tools.telegram_notifier import notify_system_online
    success = notify_system_online()
    if success:
        print('✅ Startup notification sent to Telegram')
    else:
        print('⚠️ Startup notification failed or disabled')
except Exception as e:
    print(f'⚠️ Could not send startup notification: {e}')
"
}

# Function to run startup recovery
run_startup_recovery() {
    python3 -c "
import os
import sys

# Add the repo to path if it exists
if os.path.exists('$REPO_DIR'):
    sys.path.insert(0, '$REPO_DIR')

try:
    from nova.deployment_task_manager import initialize_system
    result = initialize_system(run_recovery=True)
    if result.get('recovery_performed'):
        print('✅ Startup recovery completed')
        summary = result.get('recovery_summary', {})
        print(f'   Running tasks found: {summary.get(\"running_tasks_found\", 0)}')
        print(f'   Tasks paused: {summary.get(\"tasks_paused\", 0)}')
        print(f'   Checkpoints restored: {summary.get(\"checkpoints_restored\", 0)}')
    else:
        print('⚠️ Startup recovery not performed')
except Exception as e:
    print(f'⚠️ Startup recovery failed: {e}')
"
}

# Check if GITHUB_TOKEN and GITHUB_REPO are set
if [ -z "$GITHUB_TOKEN" ] || [ -z "$GITHUB_REPO" ]; then
    echo "⚠️ GITHUB_TOKEN or GITHUB_REPO not set. Skipping git setup."
else
    # Build the authenticated URL
    AUTH_REPO_URL="https://$GITHUB_TOKEN@github.com/$GITHUB_REPO.git"

    if [ ! -d "$REPO_DIR/.git" ]; then
        echo "📥 Cloning repository into persistent volume..."
        rm -rf "$REPO_DIR" # Clean any partial setup
        git clone "$AUTH_REPO_URL" "$REPO_DIR"
    else
        echo "🔄 Updating existing repository in persistent volume..."
        cd "$REPO_DIR"
        # Force update to remote state
        git fetch origin
        git reset --hard origin/main
    fi

    # Configure Git identity for the agent
    git config --global user.email "nova@agent.ai"
    git config --global user.name "Nova Agent"

    echo "✅ Git repository is ready at $REPO_DIR"
fi

# Switch to the repo directory if it exists, so the agent starts there
if [ -d "$REPO_DIR" ]; then
    echo "📂 Switching context to $REPO_DIR"
    cd "$REPO_DIR"
    # Ensure dependencies from the repo are installed (useful for self-improvement)
    if [ -f "requirements.txt" ]; then
        echo "📦 Updating dependencies from repository..."
        pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
    fi
else
    echo "📂 Repo directory not found, staying in /app"
    cd /app
fi

# Run startup recovery for interrupted tasks
echo "🔄 Running startup recovery..."
run_startup_recovery

# Send system online notification to Telegram
echo "📨 Sending startup notification..."
send_startup_notification

# Run the bot
echo "🤖 Launching Nova Bot..."
# We use python -m nova.telegram_bot but we must be sure the parent dir of nova package is in PYTHONPATH
export PYTHONPATH=$(pwd):$PYTHONPATH
python -m nova.telegram_bot