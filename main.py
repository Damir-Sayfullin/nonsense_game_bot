#!/usr/bin/env python3
import logging
import os
import sqlite3
import random
import string
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
            room_code TEXT UNIQUE,
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER,
            user_id INTEGER,
            message_id INTEGER,
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def generate_room_code():
    """Generate random room code"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

QUESTIONS = [
    "Какой?",
    "Кто?",
    "С кем?",
    "Где?",
    "Что делали?",
    "Что с ними стало?"
]

WAITING_FOR_ANSWER = 1
WAITING_FOR_ROOM_CODE = 2

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command"""
    keyboard = [
        [InlineKeyboardButton("🎮 Новая игра", callback_data='new_game')],
        [InlineKeyboardButton("📋 Правила", callback_data='rules')],
        [InlineKeyboardButton("🔑 Присоединиться по коду", callback_data='join_by_code')]
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
• Создай новую игру или присоединись по коду
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
    elif query.data == 'join_by_code':
        await ask_for_room_code(update, context)
    elif query.data == 'start_game':
        await start_game_session(query, context)
    elif query.data == 'leave_game':
        await leave_game(query, context)

def get_room_code_from_context(context):
    """Get room code from user context"""
    return context.user_data.get('room_code')

def set_room_code_in_context(context, code):
    """Set room code in user context"""
    context.user_data['room_code'] = code

async def start_new_game(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new game"""
    room_code = generate_room_code()
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    user_id = query.from_user.id
    
    cursor.execute('''
        INSERT INTO games (room_code, created_by, status, current_question_idx)
        VALUES (?, ?, ?, ?)
    ''', (room_code, user_id, 'waiting', 0))
    
    game_id = cursor.lastrowid
    
    cursor.execute('''
        INSERT INTO game_players (game_id, user_id, username, first_name)
        VALUES (?, ?, ?, ?)
    ''', (game_id, user_id, query.from_user.username, query.from_user.first_name))
    
    conn.commit()
    conn.close()
    
    set_room_code_in_context(context, room_code)
    context.user_data['game_id'] = game_id
    
    keyboard = [
        [InlineKeyboardButton("➕ Пригласить друзей", callback_data='copy_code')],
        [InlineKeyboardButton("▶️ Начать игру", callback_data='start_game')],
        [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await query.edit_message_text(
        text=f"🎮 <b>Комната создана!</b>\n\n"
             f"🔑 Код комнаты: <code>{room_code}</code>\n\n"
             f"👥 Игроки (1):\n"
             f"• {query.from_user.first_name}\n\n"
             f"Скажи друзьям этот код, чтобы они присоединились!",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    context.user_data['creator_message_id'] = query.message.message_id

async def ask_for_room_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask user for room code - entry point for conversation"""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            text="🔑 <b>Напиши код комнаты</b> (4 буквы/цифры)\n\n"
                 "Пример: <code>ABC1</code>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            text="🔑 <b>Напиши код комнаты</b> (4 буквы/цифры)\n\n"
                 "Пример: <code>ABC1</code>",
            parse_mode='HTML'
        )
    return WAITING_FOR_ROOM_CODE

async def receive_room_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive room code and join game"""
    if not update.message or not update.message.text:
        return WAITING_FOR_ROOM_CODE
    
    room_code = update.message.text.strip().upper()
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id FROM games 
        WHERE room_code = ? AND status = 'waiting'
    ''', (room_code,))
    
    result = cursor.fetchone()
    if not result:
        await update.message.reply_text("❌ Комната не найдена или игра уже началась.")
        conn.close()
        return ConversationHandler.END
    
    game_id = result[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM game_players 
        WHERE game_id = ? AND user_id = ?
    ''', (game_id, user_id))
    
    if cursor.fetchone()[0] > 0:
        await update.message.reply_text("❌ Ты уже в этой игре!")
        conn.close()
        return ConversationHandler.END
    
    cursor.execute('''
        INSERT INTO game_players (game_id, user_id, username, first_name)
        VALUES (?, ?, ?, ?)
    ''', (game_id, user_id, update.effective_user.username, update.effective_user.first_name))
    
    cursor.execute('''
        SELECT user_id, first_name FROM game_players WHERE game_id = ? ORDER BY joined_at
    ''', (game_id,))
    players_data = cursor.fetchall()
    players = [row[1] for row in players_data]
    creator_id = players_data[0][0] if players_data else None
    
    conn.commit()
    conn.close()
    
    set_room_code_in_context(context, room_code)
    
    keyboard = [
        [InlineKeyboardButton("➕ Приглас друзей", callback_data='copy_code')],
        [InlineKeyboardButton("▶️ Начать игру", callback_data='start_game')],
        [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    players_text = "\n".join([f"• {p}" for p in players])
    
    message_text = f"🎮 <b>Присоединился!</b>\n\n" \
                   f"🔑 Код: <code>{room_code}</code>\n\n" \
                   f"👥 Игроки ({len(players)}):\n{players_text}\n\n" \
                   f"Жди, когда начнётся игра!"
    
    message = await update.message.reply_text(
        text=message_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    context.user_data['room_message_id'] = message.message_id
    context.user_data['game_id'] = game_id
    
    try:
        if creator_id and creator_id != user_id:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM game_players WHERE game_id = ?
            ''', (game_id,))
            total_players = cursor.fetchone()[0]
            conn.close()
            
            updated_text = f"🎮 <b>Комната создана!</b>\n\n" \
                          f"🔑 Код комнаты: <code>{room_code}</code>\n\n" \
                          f"👥 Игроки ({total_players}):\n{players_text}\n\n" \
                          f"Скажи друзьям этот код, чтобы они присоединились!"
            
            await context.bot.edit_message_text(
                chat_id=creator_id,
                message_id=context.user_data.get('creator_message_id', 0),
                text=updated_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    except TelegramError:
        pass
    
    return ConversationHandler.END

async def leave_game(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Leave the game room"""
    room_code = get_room_code_from_context(context)
    user_id = query.from_user.id
    
    if not room_code:
        await query.edit_message_text("❌ Комната не найдена")
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id, created_by FROM games 
        WHERE room_code = ? AND status = 'waiting'
    ''', (room_code,))
    
    result = cursor.fetchone()
    if not result:
        await query.edit_message_text("❌ Комната не найдена или игра уже началась")
        conn.close()
        return
    
    game_id, created_by = result
    
    cursor.execute('''
        DELETE FROM game_players WHERE game_id = ? AND user_id = ?
    ''', (game_id, user_id))
    
    cursor.execute('SELECT COUNT(*) FROM game_players WHERE game_id = ?', (game_id,))
    player_count = cursor.fetchone()[0]
    
    if player_count == 0:
        cursor.execute('DELETE FROM games WHERE game_id = ?', (game_id,))
        await query.edit_message_text("👋 Ты вышел из комнаты. Комната удалена.")
    else:
        if user_id == created_by:
            cursor.execute('''
                SELECT user_id FROM game_players WHERE game_id = ? ORDER BY joined_at LIMIT 1
            ''', (game_id,))
            new_creator = cursor.fetchone()[0]
            cursor.execute('UPDATE games SET created_by = ? WHERE game_id = ?', (new_creator, game_id))
            await query.edit_message_text("👋 Ты вышел из комнаты. Новый создатель - следующий игрок.")
        else:
            await query.edit_message_text("👋 Ты вышел из комнаты.")
    
    conn.commit()
    conn.close()
    
    context.user_data.pop('room_code', None)

async def start_game_session(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the game"""
    room_code = get_room_code_from_context(context)
    user_id = query.from_user.id
    
    if not room_code:
        await query.edit_message_text("❌ Комната не найдена")
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id, created_by FROM games 
        WHERE room_code = ? AND status = 'waiting'
    ''', (room_code,))
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        return
    
    game_id, created_by = result
    
    if user_id != created_by:
        await query.edit_message_text("❌ Только создатель игры может её начать")
        conn.close()
        return
    
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
            keyboard = [[InlineKeyboardButton("✍️ Ответить", callback_data=f'answer_{game_id}_{question_idx}_{idx}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b>\n\n<b>{question}</b>",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"Failed to send message to {user_id}: {e}")

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle player's answer - convert button click to text input"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    game_id = int(data[1])
    question_idx = int(data[2])
    player_idx = int(data[3])
    
    context.user_data['current_game_id'] = game_id
    context.user_data['current_question_idx'] = question_idx
    context.user_data['current_player_idx'] = player_idx
    
    question = QUESTIONS[question_idx]
    
    await query.edit_message_text(
        text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b>\n\n<b>{question}</b>\n\n📝 <b>Напиши свой ответ в чат:</b>",
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
    user_id = update.effective_user.id
    answer = update.message.text
    
    if not game_id or question_idx is None or player_idx is None:
        await update.message.reply_text("❌ Ошибка: данные игры не найдены")
        return ConversationHandler.END
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO game_answers (game_id, question_idx, player_idx, answer)
        VALUES (?, ?, ?, ?)
    ''', (game_id, question_idx, player_idx, answer))
    
    cursor.execute('''
        SELECT COUNT(*) FROM game_players WHERE game_id = ?
    ''', (game_id,))
    total_players = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM game_answers 
        WHERE game_id = ? AND question_idx = ? AND answer IS NOT NULL
    ''', (game_id, question_idx))
    answered_count = cursor.fetchone()[0]
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Ответ сохранён!\n\nЖди других игроков...")
    
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
        entry_points=[
            CallbackQueryHandler(handle_answer, pattern=r'^answer_'),
            CallbackQueryHandler(ask_for_room_code, pattern=r'^join_by_code$')
        ],
        states={
            WAITING_FOR_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
            WAITING_FOR_ROOM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_room_code)]
        },
        fallbacks=[],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
