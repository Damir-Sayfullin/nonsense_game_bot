# Telegram Bot Project

## Overview
Python Telegram bot with SQLite database integration. The bot saves user information and messages to a database.

## Features
- `/start` - Start using the bot
- `/help` - Show help message
- `/stats` - Show personal message statistics
- Saves all messages to database
- Tracks user information

## Setup
1. Set `TELEGRAM_BOT_TOKEN` environment variable with your bot token from BotFather
2. Install dependencies: `pip install -r requirements.txt`
3. Run bot: `python bot.py`

## Database
- Uses SQLite database (`bot_data.db`)
- Tables: `users` (user profiles), `messages` (message history)

## Tech Stack
- Python 3.11
- python-telegram-bot 20.3
- SQLite3
