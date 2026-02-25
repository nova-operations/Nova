#!/bin/bash

# Configuration
REPO_DIR="/app/data/nova_repo"
SKILLS_DIR="/app/data/skills"

# Ensure /app/data exists (it should be our persistent volume)
mkdir -p /app/data
mkdir -p "$SKILLS_DIR"

echo "🚀 Starting Nova environment setup..."

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
        # Reset any local changes to ensure clean update if needed (optional)
        # git reset --hard HEAD 
        git pull "$AUTH_REPO_URL"
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
        pip install --no-cache-dir -r requirements.txt
    fi
else
    echo "📂 Repo directory not found, staying in /app"
    cd /app
fi

# Run the bot
echo "🤖 Launching Nova Bot..."
# We use python -m nova.telegram_bot but we must be sure the parent dir of nova package is in PYTHONPATH
export PYTHONPATH=$(pwd):$PYTHONPATH
python -m nova.telegram_bot
