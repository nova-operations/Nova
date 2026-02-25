#!/bin/bash
# Check Git repository health

echo "🔍 Checking Git Status..."
git status

echo "📊 Checking for unpushed commits..."
git log origin/main..main --oneline

echo "🛠️ Verifying remote URL..."
git remote -v

echo "✅ Git health check complete."
