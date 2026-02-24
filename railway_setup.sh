#!/bin/bash

# Nova Railway Setup automation script

echo "🚀 Starting Nova Railway Setup..."

# Check if railway CLI is installed
if ! command -v railway &> /dev/null
then
    echo "❌ Railway CLI not found. Please install it first: npm i -g @railway/cli"
    exit 1
fi

# Check login status
railway status &> /dev/null
if [ $? -ne 0 ]; then
    echo "🔑 Please login to Railway first:"
    railway login
fi

# Link or Init project
if [ ! -f ".railway/config.json" ]; then
    echo "📁 Initializing new Railway project..."
    railway init
else
    echo "🔗 Project already linked."
fi

# Ask if they want Postgres
read -p "❓ Do you want to add a managed PostgreSQL database? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "🐘 Adding PostgreSQL service..."
    railway add --database postgres
    echo "✅ Postgres added. DATABASE_URL will be available automatically."
else
    echo "💾 Skipping Postgres. Please ensure you manually add a Volume mounted at /app/data in the Railway UI if using SQLite."
fi

echo "⚙️ Setting up environment variables from .env if present..."
if [ -f ".env" ]; then
    # Filter out comments and empty lines, then set
    railway vars set $(grep -v '^#' .env | xargs)
    echo "✅ Variables uploaded."
else
    echo "⚠️ .env file not found. Please set variables manually in the Railway Dashboard."
fi

echo "⬆️ Deploying Nova..."
railway up

echo "🎉 Setup request sent! Check your Railway Dashboard for progress."
