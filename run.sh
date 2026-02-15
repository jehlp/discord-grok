#!/bin/bash
set -e

cd "$(dirname "$0")"

# Check if .env exists
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo ""
    echo "Please edit .env with your tokens:"
    echo "  DISCORD_TOKEN - from https://discord.com/developers/applications"
    echo "  XAI_API_KEY   - from https://console.x.ai"
    echo ""
    echo "Then run this script again."
    exit 1
fi

# Check if tokens are set
source .env
if [ "$DISCORD_TOKEN" = "your_discord_bot_token_here" ] || [ -z "$DISCORD_TOKEN" ]; then
    echo "Error: DISCORD_TOKEN not set in .env"
    exit 1
fi
if [ "$XAI_API_KEY" = "your_xai_api_key_here" ] || [ -z "$XAI_API_KEY" ]; then
    echo "Error: XAI_API_KEY not set in .env"
    exit 1
fi

# Build and run
echo "Starting Grok Discord bot..."
docker compose up --build -d
echo "Bot is running. Use 'docker compose logs -f' to view logs."
