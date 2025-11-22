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
            awaiting_question_idx INTEGER DEFAULT -1,
            is_admin INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
    ''')
    
    try:
        cursor.execute('ALTER TABLE game_players ADD COLUMN awaiting_question_idx INTEGER DEFAULT -1')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE game_players ADD COLUMN is_admin INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    
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

def get_players_list_text(game_id, conn):
    """Get formatted player list with admin crown"""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT first_name, is_admin FROM game_players WHERE game_id = ? ORDER BY joined_at
    ''', (game_id,))
    players_data = cursor.fetchall()
    
    players_text = ""
    for name, is_admin in players_data:
        if is_admin:
            players_text += f"• {name} 👑\n"
        else:
            players_text += f"• {name}\n"
    return players_text.strip()

async def update_room_players(game_id, room_code, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update all players in room with current player list"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Get all players
    cursor.execute('''
        SELECT user_id, first_name, is_admin FROM game_players WHERE game_id = ? ORDER BY joined_at
    ''', (game_id,))
    players_data = cursor.fetchall()
    
    # Build player list text
    players_list = ""
    for first_name, is_admin in [(p[1], p[2]) for p in players_data]:
        if is_admin:
            players_list += f"• {first_name} 👑\n"
        else:
            players_list += f"• {first_name}\n"
    players_list = players_list.strip()
    
    # Update each player
    for user_id, first_name, is_admin in players_data:
        if is_admin:
            keyboard = [
                [InlineKeyboardButton("▶️ Начать игру", callback_data='start_game')],
                [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = f"🎮 <b>Комната создана!</b>\n\n" \
                      f"🔑 Код комнаты: <code>{room_code}</code>\n\n" \
                      f"👥 Игроки ({len(players_data)}):\n{players_list}\n\n" \
                      f"Скажи друзьям этот код, чтобы они присоединились!"
        
        # Check if we have an existing message for this user
        cursor.execute('''
            SELECT message_id FROM game_messages WHERE game_id = ? AND user_id = ?
        ''', (game_id, user_id))
        message_row = cursor.fetchone()
        
        try:
            if message_row:
                # Edit existing message
                message_id = message_row[0]
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=message_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                # Send new message and store message ID
                msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                cursor.execute('''
                    INSERT INTO game_messages (game_id, user_id, message_id)
                    VALUES (?, ?, ?)
                ''', (game_id, user_id, msg.message_id))
                conn.commit()
        except TelegramError as e:
            logger.error(f"Failed to update message for {user_id}: {e}")
    
    conn.close()

async def start_new_game(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new game"""
    room_code = get_room_code_from_context(context)
    
    # If we have a room code, we're restarting an existing room
    if room_code:
        await start_new_game_in_room(query, context, room_code)
        return
    
    # Otherwise, create a brand new game
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
        INSERT INTO game_players (game_id, user_id, username, first_name, is_admin)
        VALUES (?, ?, ?, ?, 1)
    ''', (game_id, user_id, query.from_user.username, query.from_user.first_name))
    
    conn.commit()
    conn.close()
    
    set_room_code_in_context(context, room_code)
    context.user_data['game_id'] = game_id
    
    keyboard = [
        [InlineKeyboardButton("▶️ Начать игру", callback_data='start_game')],
        [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await query.edit_message_text(
        text=f"🎮 <b>Комната создана!</b>\n\n"
             f"🔑 Код комнаты: <code>{room_code}</code>\n\n"
             f"👥 Игроки (1):\n"
             f"• {query.from_user.first_name} 👑\n\n"
             f"Скажи друзьям этот код, чтобы они присоединились!",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    # Store message ID for future edits
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO game_messages (game_id, user_id, message_id)
        VALUES (?, ?, ?)
    ''', (game_id, query.from_user.id, query.message.message_id))
    conn.commit()
    conn.close()
    
    context.user_data['creator_message_id'] = query.message.message_id

async def start_new_game_in_room(query, context: ContextTypes.DEFAULT_TYPE, room_code: str) -> None:
    """Start a new game in an existing room (after completion)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id FROM games WHERE room_code = ? AND status = 'completed'
    ''', (room_code,))
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        await query.edit_message_text("❌ Не удалось начать новую игру")
        return
    
    old_game_id = result[0]
    
    cursor.execute('''
        SELECT created_by FROM games WHERE game_id = ?
    ''', (old_game_id,))
    created_by = cursor.fetchone()[0]
    
    # Create new game with same room code
    cursor.execute('''
        INSERT INTO games (room_code, created_by, status, current_question_idx)
        VALUES (?, ?, ?, ?)
    ''', (room_code, created_by, 'waiting', 0))
    
    new_game_id = cursor.lastrowid
    
    # Copy players from old game to new game with admin status preserved
    cursor.execute('''
        SELECT user_id, username, first_name, is_admin FROM game_players 
        WHERE game_id = ? ORDER BY joined_at
    ''', (old_game_id,))
    
    players = cursor.fetchall()
    for user_id, username, first_name, is_admin in players:
        cursor.execute('''
            INSERT INTO game_players (game_id, user_id, username, first_name, is_admin)
            VALUES (?, ?, ?, ?, ?)
        ''', (new_game_id, user_id, username, first_name, is_admin))
    
    conn.commit()
    conn.close()
    
    context.user_data['game_id'] = new_game_id
    await query.edit_message_text("🎮 <b>Новая игра начинается в той же комнате!</b>\n\nЖди, когда админ начнёт игру.")
    
    await update_room_players(new_game_id, room_code, context)

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
        INSERT INTO game_players (game_id, user_id, username, first_name, is_admin)
        VALUES (?, ?, ?, ?, 0)
    ''', (game_id, user_id, update.effective_user.username, update.effective_user.first_name))
    
    conn.commit()
    conn.close()
    
    set_room_code_in_context(context, room_code)
    context.user_data['game_id'] = game_id
    
    keyboard = [
        [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        text=f"🎮 <b>Присоединился!</b>\n\n"
             f"🔑 Код: <code>{room_code}</code>\n\n"
             f"Жди, когда начнётся игра!",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    context.user_data['room_message_id'] = message.message_id
    
    # Store message ID for this player
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO game_messages (game_id, user_id, message_id)
        VALUES (?, ?, ?)
    ''', (game_id, user_id, message.message_id))
    conn.commit()
    conn.close()
    
    await update_room_players(game_id, room_code, context)
    
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
        conn.commit()
        conn.close()
    else:
        if user_id == created_by:
            cursor.execute('''
                SELECT user_id, id FROM game_players WHERE game_id = ? ORDER BY joined_at LIMIT 1
            ''', (game_id,))
            new_creator_data = cursor.fetchone()
            new_creator_id = new_creator_data[0]
            new_creator_player_id = new_creator_data[1]
            
            cursor.execute('UPDATE games SET created_by = ? WHERE game_id = ?', (new_creator_id, game_id))
            cursor.execute('UPDATE game_players SET is_admin = 1 WHERE id = ?', (new_creator_player_id,))
            await query.edit_message_text("👋 Ты вышел из комнаты. Новый создатель - следующий игрок.")
        else:
            await query.edit_message_text("👋 Ты вышел из комнаты.")
        
        conn.commit()
        conn.close()
        
        await update_room_players(game_id, room_code, context)
    
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
        SELECT id, user_id, first_name FROM game_players WHERE game_id = ?
    ''', (game_id,))
    
    players = cursor.fetchall()
    
    if question_idx >= len(QUESTIONS):
        conn.close()
        await generate_stories(game_id, context)
        return
    
    question = QUESTIONS[question_idx]
    
    for player_id, user_id, first_name in players:
        cursor.execute('''
            UPDATE game_players SET awaiting_question_idx = ? WHERE id = ?
        ''', (question_idx, player_id))
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b>\n\n<b>{question}</b>\n\n📝 Напиши свой ответ в чат:",
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
    
    conn.commit()
    conn.close()

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
    
    user_id = update.effective_user.id
    answer = update.message.text
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id, awaiting_question_idx, id FROM game_players 
        WHERE user_id = ? AND awaiting_question_idx >= 0
        LIMIT 1
    ''', (user_id,))
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        return WAITING_FOR_ANSWER
    
    game_id, question_idx, player_idx = result
    
    cursor.execute('''
        INSERT OR REPLACE INTO game_answers (game_id, question_idx, player_idx, answer)
        VALUES (?, ?, ?, ?)
    ''', (game_id, question_idx, player_idx, answer))
    
    cursor.execute('''
        UPDATE game_players SET awaiting_question_idx = -1 WHERE id = ?
    ''', (player_idx,))
    
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
    
    await update.message.reply_text("✅ Ответ сохранён!\n\nЖди других игроков...")
    
    if answered_count >= total_players:
        await send_question_to_players(game_id, question_idx + 1, context)
    
    conn.close()
    return WAITING_FOR_ANSWER

async def handle_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any text message - check if it's an answer to a question"""
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    answer = update.message.text
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT game_id, awaiting_question_idx, id FROM game_players 
        WHERE user_id = ? AND awaiting_question_idx >= 0
        LIMIT 1
    ''', (user_id,))
    
    result = cursor.fetchone()
    if not result:
        cursor.execute('''
            SELECT awaiting_question_idx FROM game_players 
            WHERE user_id = ?
            LIMIT 1
        ''', (user_id,))
        user_result = cursor.fetchone()
        conn.close()
        
        if user_result and user_result[0] < 0:
            await update.message.reply_text("⏳ Пока ждёшь своего вопроса, молчишь! 🤐")
        return
    
    game_id, question_idx, player_idx = result
    
    cursor.execute('''
        INSERT OR REPLACE INTO game_answers (game_id, question_idx, player_idx, answer)
        VALUES (?, ?, ?, ?)
    ''', (game_id, question_idx, player_idx, answer))
    
    cursor.execute('''
        UPDATE game_players SET awaiting_question_idx = -1 WHERE id = ?
    ''', (player_idx,))
    
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
    
    await update.message.reply_text("✅ Ответ сохранён!\n\nЖди других игроков...")
    
    if answered_count >= total_players:
        await send_question_to_players(game_id, question_idx + 1, context)
    
    conn.close()

async def generate_stories(game_id, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send multiple rotated stories to all players"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, user_id, first_name FROM game_players WHERE game_id = ?
        ORDER BY id
    ''', (game_id,))
    
    players = cursor.fetchall()
    num_players = len(players)
    
    cursor.execute('''
        SELECT question_idx, player_idx, answer FROM game_answers 
        WHERE game_id = ? ORDER BY question_idx, player_idx
    ''', (game_id,))
    
    all_answers = cursor.fetchall()
    
    cursor.execute('''
        SELECT room_code FROM games WHERE game_id = ?
    ''', (game_id,))
    room_code = cursor.fetchone()[0]
    
    cursor.execute('UPDATE games SET status = ? WHERE game_id = ?', ('completed', game_id))
    conn.commit()
    conn.close()
    
    player_ids = [p[0] for p in players]
    
    all_stories = "🎉 <b>ИСТОРИИ:</b>\n\n"
    
    for story_num in range(num_players):
        story_text = build_rotated_story(all_answers, story_num, num_players, player_ids)
        all_stories += f"{story_text}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("▶️ Начать новую игру", callback_data='new_game')],
        [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    for player_id, user_id, first_name in players:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{all_stories}\n\nДобавляйте друзей по коду и играйте снова!",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"Failed to send stories to {user_id}: {e}")
    
    # Clear game messages so update_room_players sends new messages instead of editing
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM game_messages WHERE game_id = ?', (game_id,))
    conn.commit()
    conn.close()
    
    # Show room status with player list
    await update_room_players(game_id, room_code, context)

def build_rotated_story(all_answers, story_num, num_players, player_ids):
    """Build a story with rotated player order"""
    story_answers = {}
    for q_idx, p_idx, answer in all_answers:
        story_answers[(q_idx, p_idx)] = answer
    
    words = []
    for q_idx in range(len(QUESTIONS)):
        player_idx_in_rotation = (story_num + q_idx) % num_players
        actual_player_id = player_ids[player_idx_in_rotation]
        
        if (q_idx, actual_player_id) in story_answers:
            words.append(story_answers[(q_idx, actual_player_id)])
        else:
            words.append("—")
    
    story = " ".join(words)
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
            CallbackQueryHandler(ask_for_room_code, pattern=r'^join_by_code$')
        ],
        states={
            WAITING_FOR_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
            WAITING_FOR_ROOM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_room_code)]
        },
        fallbacks=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer),
            CallbackQueryHandler(ask_for_room_code, pattern=r'^join_by_code$')
        ],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_any_text))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
