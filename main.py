#!/usr/bin/env python3
import logging
import os
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import TelegramError

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_FILE = 'game_data.db'

def init_db():
    """Initialize database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            created_by INTEGER,
            status TEXT,
            current_question_idx INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER,
            question_idx INTEGER,
            player_idx INTEGER,
            answer TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

QUESTIONS = [
    "Какой?",
    "Кто?",
    "С кем?",
    "Где?",
    "Что делали?",
    "Что с ними стало?"
]

WAITING_FOR_ANSWER = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command"""
    keyboard = [
        [InlineKeyboardButton("Новая игра", callback_data='new_game')],
        [InlineKeyboardButton("Правила", callback_data='rules')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎮 Привет! Добро пожаловать в игру <b>Чепуха</b>!\n\n"
        "Весёлая игра для компании, где вы пишете слова и получается смешная история.",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show game rules"""
    rules_text = """😄 <b>Как играть в Чепуху?</b>

Это легко и весело! Вот что нужно делать:

📝 <b>Как это работает:</b>
• Пригласи друзей в игру (минимум 2 человека)
• По очереди все отвечают на вопросы
• Главное — никто не видит ответов других! 🤐

❓ <b>Какие вопросы:</b>
"Какой?", "Кто?", "С кем?", "Где?", "Что делали?", "Что с ними стало?"

🎉 <b>И вот что будет:</b>
Каждый получит свою уникальную смешную историю, составленную из всех ответов!

Готовы? Нажимайте "Новая игра" и начинайте веселиться! 🎮"""
    await update.callback_query.edit_message_text(
        text=rules_text,
        parse_mode='HTML'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'new_game':
        await start_new_game(query, context)
    elif query.data == 'rules':
        await rules(update, context)
    elif query.data == 'join_game':
        await join_game(query, context)
    elif query.data == 'start_game':
        await start_game_session(query, context)
    elif query.data.startswith('answer_'):
        await handle_answer(query, context)

async def start_new_game(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new game"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    
    cursor.execute('''
        INSERT INTO games (chat_id, created_by, status, current_question_idx)
        VALUES (?, ?, ?, ?)
    ''', (chat_id, user_id, 'waiting', 0))
    
    game_id = cursor.lastrowid
    
    cursor.execute('''
        INSERT INTO game_players (game_id, user_id, username, first_name)
        VALUES (?, ?, ?, ?)
    ''', (game_id, user_id, query.from_user.username, query.from_user.first_name))
    
    conn.commit()
    conn.close()
    
    context.chat_data['game_id'] = game_id
    
    keyboard = [
        [InlineKeyboardButton("Присоединиться", callback_data='join_game')],
        [InlineKeyboardButton("Начать игру", callback_data='start_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=f"🎮 Новая игра создана!\n\nИгроков: 1 ({query.from_user.first_name})\n\nНужно минимум 2 игрока для начала.",
        reply_markup=reply_markup
    )

async def join_game(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Join an existing game"""
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id FROM games 
        WHERE chat_id = ? AND status = 'waiting'
        ORDER BY created_at DESC LIMIT 1
    ''', (chat_id,))
    
    result = cursor.fetchone()
    if not result:
        await query.edit_message_text("❌ Нет активной игры в этом чате.")
        conn.close()
        return
    
    game_id = result[0]
    
    cursor.execute('SELECT COUNT(*) FROM game_players WHERE game_id = ?', (game_id,))
    count = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM game_players 
        WHERE game_id = ? AND user_id = ?
    ''', (game_id, user_id))
    
    if cursor.fetchone()[0] > 0:
        await query.edit_message_text("❌ Вы уже присоединились к этой игре.")
        conn.close()
        return
    
    cursor.execute('''
        INSERT INTO game_players (game_id, user_id, username, first_name)
        VALUES (?, ?, ?, ?)
    ''', (game_id, user_id, query.from_user.username, query.from_user.first_name))
    
    conn.commit()
    
    cursor.execute('''
        SELECT first_name FROM game_players WHERE game_id = ?
    ''', (game_id,))
    players = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    context.chat_data['game_id'] = game_id
    
    keyboard = [
        [InlineKeyboardButton("Присоединиться", callback_data='join_game')],
        [InlineKeyboardButton("Начать игру", callback_data='start_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    players_text = "\n".join([f"• {p}" for p in players])
    
    await query.edit_message_text(
        text=f"🎮 Игроки ({len(players)}):\n{players_text}\n\nМинимум 2 игрока для начала.",
        reply_markup=reply_markup
    )

async def start_game_session(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the game"""
    chat_id = query.message.chat_id
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id FROM games 
        WHERE chat_id = ? AND status = 'waiting'
        ORDER BY created_at DESC LIMIT 1
    ''', (chat_id,))
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        return
    
    game_id = result[0]
    
    cursor.execute('SELECT COUNT(*) FROM game_players WHERE game_id = ?', (game_id,))
    player_count = cursor.fetchone()[0]
    
    if player_count < 2:
        await query.edit_message_text("❌ Нужно минимум 2 игрока для начала игры.")
        conn.close()
        return
    
    cursor.execute('''
        UPDATE games SET status = 'in_progress', current_question_idx = 0
        WHERE game_id = ?
    ''', (game_id,))
    
    conn.commit()
    conn.close()
    
    context.chat_data['game_id'] = game_id
    
    await query.edit_message_text("🎮 Игра начинается!\n\nПроверьте личные сообщения для ответа на первый вопрос.")
    
    await send_question_to_players(game_id, 0, context)

async def send_question_to_players(game_id, question_idx, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send current question to all players"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, first_name FROM game_players WHERE game_id = ?
    ''', (game_id,))
    
    players = cursor.fetchall()
    conn.close()
    
    if question_idx >= len(QUESTIONS):
        await generate_stories(game_id, context)
        return
    
    question = QUESTIONS[question_idx]
    
    for idx, (user_id, first_name) in enumerate(players):
        try:
            keyboard = [[InlineKeyboardButton("Ответить", callback_data=f'answer_{game_id}_{question_idx}_{idx}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b>\n\n<b>{question}</b>",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"Failed to send message to {user_id}: {e}")

async def handle_answer(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle player's answer"""
    data = query.data.split('_')
    game_id = int(data[1])
    question_idx = int(data[2])
    player_idx = int(data[3])
    
    context.user_data['current_game_id'] = game_id
    context.user_data['current_question_idx'] = question_idx
    context.user_data['current_player_idx'] = player_idx
    
    question = QUESTIONS[question_idx]
    
    await query.edit_message_text(
        text=f"❓ <b>Вопрос: {question}</b>\n\nНапишите ваш ответ:",
        parse_mode='HTML'
    )
    
    return WAITING_FOR_ANSWER

async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and save the answer"""
    if not update.message or not update.message.text:
        return WAITING_FOR_ANSWER
    
    game_id = context.user_data.get('current_game_id')
    question_idx = context.user_data.get('current_question_idx')
    player_idx = context.user_data.get('current_player_idx')
    answer = update.message.text
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO game_answers (game_id, question_idx, player_idx, answer)
        VALUES (?, ?, ?, ?)
    ''', (game_id, question_idx, player_idx, answer))
    
    cursor.execute('''
        SELECT COUNT(*) FROM game_players WHERE game_id = ?
    ''', (game_id,))
    total_players = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(DISTINCT player_idx) FROM game_answers 
        WHERE game_id = ? AND question_idx = ?
    ''', (game_id, question_idx))
    answered_count = cursor.fetchone()[0]
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Ответ сохранён!")
    
    if answered_count >= total_players:
        await send_question_to_players(game_id, question_idx + 1, context)
    
    return ConversationHandler.END

async def generate_stories(game_id, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send stories to all players"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, first_name FROM game_players WHERE game_id = ?
    ''', (game_id,))
    
    players = cursor.fetchall()
    
    cursor.execute('''
        SELECT question_idx, player_idx, answer FROM game_answers 
        WHERE game_id = ? ORDER BY player_idx, question_idx
    ''', (game_id,))
    
    answers_by_player = {}
    for row in cursor.fetchall():
        q_idx, p_idx, answer = row
        if p_idx not in answers_by_player:
            answers_by_player[p_idx] = {}
        answers_by_player[p_idx][q_idx] = answer
    
    cursor.execute('UPDATE games SET status = ? WHERE game_id = ?', ('completed', game_id))
    conn.commit()
    conn.close()
    
    for idx, (user_id, first_name) in enumerate(players):
        story_text = build_story(answers_by_player.get(idx, {}), first_name)
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 <b>Ваша история:</b>\n\n{story_text}",
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"Failed to send story to {user_id}: {e}")

def build_story(answers, player_name):
    """Build a funny story from answers"""
    words = [
        answers.get(0, "неизвестный"),
        answers.get(1, "персонаж"),
        answers.get(2, "друг"),
        answers.get(3, "место"),
        answers.get(4, "действие"),
        answers.get(5, "результат")
    ]
    
    story = (
        f"<b>{player_name}</b>, однажды <b>{words[0]}</b> <b>{words[1]}</b> встретил "
        f"<b>{words[2]}</b> <b>{words[3]}</b>. Они начали <b>{words[4]}</b>. "
        f"В результате <b>{words[5]}</b>!"
    )
    
    return story

def main() -> None:
    """Start the bot"""
    init_db()
    
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        print("ERROR: Please set TELEGRAM_BOT_TOKEN environment variable")
        return
    
    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_answer, pattern=r'^answer_')],
        states={
            WAITING_FOR_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)]
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(conv_handler)

    logger.info("Bot started polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
