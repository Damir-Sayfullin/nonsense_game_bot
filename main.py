#!/usr/bin/env python3
import logging
import os
import sqlite3
import random
import string
import asyncio
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
    user_id = update.effective_user.id
    
    # Check if user is in an active game
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT g.status FROM game_players gp
        JOIN games g ON gp.game_id = g.game_id
        WHERE gp.user_id = ? AND g.status != 'completed'
        LIMIT 1
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        await update.message.reply_text(
            "⏳ <b>Ты уже в игре!</b>\n\n"
            "Завершите текущую игру перед тем, как начинать новую. "
            "Нажми ❌ Выйти, если хочешь покинуть комнату.",
            parse_mode='HTML'
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("🎮 Новая игра", callback_data='new_game')],
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

🎉 <b>И вот что будет:</b>
Смешные и забавные истории из всех ваших ответов!

Готовы? Нажимайте "Новая игра" и начинайте веселиться! 🎮"""
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=rules_text,
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            text=rules_text,
            parse_mode='HTML'
        )

async def reset_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset broken game - delete room entirely (available for all players)"""
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Find all games where this user is playing
    cursor.execute('''
        SELECT g.game_id, g.room_code FROM games g
        JOIN game_players gp ON g.game_id = gp.game_id
        WHERE gp.user_id = ?
    ''', (user_id,))
    
    games = cursor.fetchall()
    
    if not games:
        await update.message.reply_text("❌ Ты не участвуешь ни в одной комнате.")
        conn.close()
        return
    
    # Delete all games for this user
    deleted_rooms = []
    for game_id, room_code in games:
        cursor.execute('DELETE FROM game_messages WHERE game_id = ?', (game_id,))
        cursor.execute('DELETE FROM game_answers WHERE game_id = ?', (game_id,))
        cursor.execute('DELETE FROM game_players WHERE game_id = ?', (game_id,))
        cursor.execute('DELETE FROM games WHERE game_id = ?', (game_id,))
        deleted_rooms.append(room_code)
    
    conn.commit()
    conn.close()
    
    # Clear room code from context
    context.user_data.pop('room_code', None)
    context.user_data.pop('game_id', None)
    
    rooms_text = ", ".join([f"<code>{room}</code>" for room in deleted_rooms])
    await update.message.reply_text(
        f"✅ <b>Комната(ы) удалена!</b>\n\n"
        f"Удалённые комнаты: {rooms_text}",
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
    logger.info(f"[UPDATE_ROOM_PLAYERS] Called with game_id={game_id}, room_code={room_code}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Get game status
    cursor.execute('SELECT status FROM games WHERE game_id = ?', (game_id,))
    game_status_row = cursor.fetchone()
    game_status = game_status_row[0] if game_status_row else 'waiting'
    logger.info(f"[UPDATE_ROOM_PLAYERS] Game status: {game_status}")
    
    # Get all players
    cursor.execute('''
        SELECT user_id, first_name, is_admin FROM game_players WHERE game_id = ? ORDER BY joined_at
    ''', (game_id,))
    players_data = cursor.fetchall()
    logger.info(f"[UPDATE_ROOM_PLAYERS] Found {len(players_data)} players: {players_data}")
    
    # Build player list text
    players_list = ""
    for first_name, is_admin in [(p[1], p[2]) for p in players_data]:
        if is_admin:
            players_list += f"• {first_name} 👑\n"
        else:
            players_list += f"• {first_name}\n"
    players_list = players_list.strip()
    logger.info(f"[UPDATE_ROOM_PLAYERS] Player list text:\n{players_list}")
    
    # If game is completed, delete old messages to force sending new ones
    if game_status == 'completed':
        logger.info(f"[UPDATE_ROOM_PLAYERS] Game is completed, clearing old messages")
        cursor.execute('DELETE FROM game_messages WHERE game_id = ?', (game_id,))
        conn.commit()
    
    # Update each player
    for user_id, first_name, is_admin in players_data:
        logger.info(f"[UPDATE_ROOM_PLAYERS] Processing player {first_name} (user_id={user_id}, is_admin={is_admin})")
        if is_admin:
            if game_status == 'completed':
                keyboard = [
                    [InlineKeyboardButton("▶️ Начать новую игру", callback_data='new_game')],
                    [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
                ]
            else:
                # Game is waiting - show start game button
                keyboard = [
                    [InlineKeyboardButton("▶️ Начать игру", callback_data='start_game')],
                    [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
                ]
        else:
            keyboard = [
                [InlineKeyboardButton("❌ Выйти", callback_data='leave_game')]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Different message text based on game status
        if game_status == 'completed':
            message_text = f"🎉 <b>Игра закончена!</b>\n\n" \
                          f"🔑 Код комнаты: <code>{room_code}</code>\n\n" \
                          f"👥 Игроки ({len(players_data)}):\n{players_list}"
        else:
            message_text = f"🎮 <b>Комната создана!</b>\n\n" \
                          f"🔑 Код комнаты: <code>{room_code}</code>\n\n" \
                          f"👥 Игроки ({len(players_data)}):\n{players_list}\n\n" \
                          f"Скажи друзьям этот код, чтобы они присоединились!"
        
        logger.info(f"[UPDATE_ROOM_PLAYERS] Message text for {first_name}:\n{message_text}")
        
        # Check if we have an existing message for this user
        cursor.execute('''
            SELECT message_id FROM game_messages WHERE game_id = ? AND user_id = ?
        ''', (game_id, user_id))
        message_row = cursor.fetchone()
        logger.info(f"[UPDATE_ROOM_PLAYERS] Existing message_row for user {user_id}: {message_row}")
        
        try:
            if message_row and game_status != 'completed':
                # Edit existing message only if game is not completed
                message_id = message_row[0]
                logger.info(f"[UPDATE_ROOM_PLAYERS] Editing message {message_id} for user {user_id}")
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=message_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                logger.info(f"[UPDATE_ROOM_PLAYERS] Successfully edited message for user {user_id}")
            else:
                # Send new message and store message ID
                logger.info(f"[UPDATE_ROOM_PLAYERS] Sending new message to user {user_id}")
                msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                logger.info(f"[UPDATE_ROOM_PLAYERS] Message sent with ID {msg.message_id}")
                cursor.execute('''
                    INSERT INTO game_messages (game_id, user_id, message_id)
                    VALUES (?, ?, ?)
                ''', (game_id, user_id, msg.message_id))
                conn.commit()
        except TelegramError as e:
            logger.error(f"[UPDATE_ROOM_PLAYERS] Failed to update message for {user_id}: {e}")
    
    logger.info(f"[UPDATE_ROOM_PLAYERS] Completed for game_id={game_id}")
    conn.close()

async def start_new_game(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new game"""
    room_code = get_room_code_from_context(context)
    user_id = query.from_user.id
    
    # If we have a room code, check if we're the creator and can restart it
    if room_code:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT created_by FROM games WHERE room_code = ? AND status = 'completed'
        ''', (room_code,))
        result = cursor.fetchone()
        conn.close()
        
        # Only restart the room if we're the creator and game is completed
        if result and result[0] == user_id:
            await start_new_game_in_room(query, context, room_code)
            return
        
        # Clear the room code if we can't restart it
        context.user_data.pop('room_code', None)
    
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
    ''', (game_id, query.from_user.id, message.message_id))
    conn.commit()
    conn.close()
    
    context.user_data['creator_message_id'] = message.message_id

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
    
    # Copy players from old game to preserve admin status
    cursor.execute('''
        SELECT user_id, username, first_name, is_admin FROM game_players 
        WHERE game_id = ? ORDER BY joined_at
    ''', (old_game_id,))
    players = cursor.fetchall()
    
    # Delete old game data to free up the room_code for reuse
    cursor.execute('DELETE FROM game_messages WHERE game_id = ?', (old_game_id,))
    cursor.execute('DELETE FROM game_answers WHERE game_id = ?', (old_game_id,))
    cursor.execute('DELETE FROM game_players WHERE game_id = ?', (old_game_id,))
    cursor.execute('DELETE FROM games WHERE game_id = ?', (old_game_id,))
    
    # Create new game with same room code
    cursor.execute('''
        INSERT INTO games (room_code, created_by, status, current_question_idx)
        VALUES (?, ?, ?, ?)
    ''', (room_code, created_by, 'waiting', 0))
    
    new_game_id = cursor.lastrowid
    
    # Add players to new game with preserved admin status
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
    
    # Send confirmation message first
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
    
    # Store message ID for this player (delete old one first if exists)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM game_messages WHERE game_id = ? AND user_id = ?', (game_id, user_id))
    cursor.execute('''
        INSERT INTO game_messages (game_id, user_id, message_id)
        VALUES (?, ?, ?)
    ''', (game_id, user_id, message.message_id))
    conn.commit()
    conn.close()
    
    logger.info(f"[RECEIVE_ROOM_CODE] Player {user_id} joined game {game_id} with code {room_code}, message_id={message.message_id}")
    
    # Update room players - will edit the message we just sent
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
        cursor.execute('DELETE FROM game_messages WHERE game_id = ?', (game_id,))
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
        
        # Не удаляем game_messages - это позволяет отредактировать старое сообщение при присоединении
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

async def end_game_due_to_inactivity(game_id, inactive_user_id, inactive_first_name, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End game because a player was inactive"""
    logger.info(f"[INACTIVITY] Ending game {game_id} due to inactivity of {inactive_first_name}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Check if game is already aborted - if so, don't process again
    cursor.execute('SELECT status FROM games WHERE game_id = ?', (game_id,))
    status_row = cursor.fetchone()
    if status_row and status_row[0] == 'aborted':
        logger.info(f"[INACTIVITY] Game {game_id} already aborted, skipping duplicate timeout")
        conn.close()
        return
    
    # Get all players (including inactive ones)
    cursor.execute('''
        SELECT user_id, first_name FROM game_players 
        WHERE game_id = ?
    ''', (game_id,))
    all_players = cursor.fetchall()
    
    # Find players who haven't answered current question
    cursor.execute('''
        SELECT g.game_id FROM games g WHERE g.game_id = ?
    ''', (game_id,))
    cursor.execute('''
        SELECT current_question_idx FROM games WHERE game_id = ?
    ''', (game_id,))
    question_row = cursor.fetchone()
    current_question = question_row[0] if question_row else 0
    
    # Get all players who didn't answer this question
    cursor.execute('''
        SELECT DISTINCT gp.first_name FROM game_players gp
        WHERE gp.game_id = ? AND gp.awaiting_question_idx = ?
    ''', (game_id, current_question))
    inactive_players = [row[0] for row in cursor.fetchall()]
    
    # If no inactive players found, use the provided one
    if not inactive_players:
        inactive_players = [inactive_first_name]
    
    # Delete inactive players
    cursor.execute('DELETE FROM game_players WHERE game_id = ?', (game_id,))
    cursor.execute('UPDATE games SET status = ? WHERE game_id = ?', ('aborted', game_id))
    conn.commit()
    conn.close()
    
    # Create message with all inactive players listed with commas
    inactive_list = ", ".join(f"<b>{name}</b>" for name in inactive_players)
    message = f"⏱️ <b>Игра отменена!</b>\n\n❌ Игрок(и) {inactive_list} не ответили в течение 2 минут и были исключены из игры.\n\nИгра закончена."
    
    # Send one message to all players (including inactive ones)
    for user_id, first_name in all_players:
        try:
            await context.bot.send_message(chat_id=user_id, text=message, parse_mode='HTML')
        except TelegramError as e:
            logger.error(f"Failed to notify {first_name}: {e}")

async def start_inactivity_timeout(game_id, user_id, first_name, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a 2-minute inactivity timeout for a player"""
    await asyncio.sleep(120)  # 2 minutes
    await end_game_due_to_inactivity(game_id, user_id, first_name, context)

async def send_question_to_players(game_id, question_idx, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send current question to all players"""
    logger.info(f"[SEND_QUESTION_TO_PLAYERS] Called with game_id={game_id}, question_idx={question_idx}, total_questions={len(QUESTIONS)}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, user_id, first_name FROM game_players WHERE game_id = ?
    ''', (game_id,))
    
    players = cursor.fetchall()
    total_players = len(players)
    
    if question_idx >= len(QUESTIONS):
        logger.info(f"[SEND_QUESTION_TO_PLAYERS] All questions answered! Calling generate_stories")
        conn.close()
        await generate_stories(game_id, context)
        return
    
    logger.info(f"[SEND_QUESTION_TO_PLAYERS] Sending question {question_idx} to {total_players} players")
    
    question = QUESTIONS[question_idx]
    
    # Prepare all player updates first
    updates = []
    for player_id, user_id, first_name in players:
        cursor.execute('''
            UPDATE game_players SET awaiting_question_idx = ? WHERE id = ?
        ''', (question_idx, player_id))
        updates.append((user_id, first_name, player_id))
    
    conn.commit()
    conn.close()
    
    # Now send messages AFTER closing database
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    for user_id, first_name, player_id in updates:
        try:
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b> (0/{total_players} ответили)\n\n<b>{question}</b>\n\n📝 Напиши свой ответ в чат:",
                parse_mode='HTML'
            )
            # Delete old message records and store new message ID
            cursor.execute('Delete FROM game_messages WHERE game_id = ? AND user_id = ?', (game_id, user_id))
            cursor.execute('''
                INSERT INTO game_messages (game_id, user_id, message_id)
                VALUES (?, ?, ?)
            ''', (game_id, user_id, msg.message_id))
            conn.commit()
            
            # Start inactivity timeout for this player
            asyncio.create_task(start_inactivity_timeout(game_id, user_id, first_name, context))
        except TelegramError as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
    
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
    
    # Get all players to update their question messages with progress
    cursor.execute('''
        SELECT user_id FROM game_players WHERE game_id = ?
    ''', (game_id,))
    all_player_ids = [row[0] for row in cursor.fetchall()]
    
    # Get the question text
    question = QUESTIONS[question_idx]
    
    # Update question message for all players with new progress
    for player_user_id in all_player_ids:
        cursor.execute('''
            SELECT message_id FROM game_messages WHERE game_id = ? AND user_id = ?
        ''', (game_id, player_user_id))
        msg_row = cursor.fetchone()
        
        if msg_row:
            message_id = msg_row[0]
            try:
                await context.bot.edit_message_text(
                    chat_id=player_user_id,
                    message_id=message_id,
                    text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b> ({answered_count}/{total_players} ответили)\n\n<b>{question}</b>\n\n📝 Напиши свой ответ в чат:",
                    parse_mode='HTML'
                )
            except TelegramError as e:
                logger.error(f"Failed to update progress for {player_user_id}: {e}")
    
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
    
    # Cancel any pending inactivity timeout for this user
    key = f"timeout_{user_id}"
    if key in context.user_data and context.user_data[key]:
        context.user_data[key].cancel()
        context.user_data[key] = None
    
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
    
    # Save answer and update player status
    cursor.execute('''
        INSERT OR REPLACE INTO game_answers (game_id, question_idx, player_idx, answer)
        VALUES (?, ?, ?, ?)
    ''', (game_id, question_idx, player_idx, answer))
    
    cursor.execute('''
        UPDATE game_players SET awaiting_question_idx = -1 WHERE id = ?
    ''', (player_idx,))
    
    # Get counts and all player info BEFORE closing DB
    cursor.execute('''
        SELECT COUNT(*) FROM game_players WHERE game_id = ?
    ''', (game_id,))
    total_players = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM game_answers 
        WHERE game_id = ? AND question_idx = ? AND answer IS NOT NULL
    ''', (game_id, question_idx))
    answered_count = cursor.fetchone()[0]
    
    # Get all players and their message IDs
    cursor.execute('''
        SELECT gp.user_id, gm.message_id FROM game_players gp
        LEFT JOIN game_messages gm ON gp.game_id = gm.game_id AND gp.user_id = gm.user_id
        WHERE gp.game_id = ?
    ''', (game_id,))
    player_messages = cursor.fetchall()
    
    question = QUESTIONS[question_idx]
    
    conn.commit()
    conn.close()
    
    # Send reply first
    await update.message.reply_text("✅ Ответ сохранён!\n\nЖди других игроков...")
    
    # Now update question messages for all players AFTER closing DB
    for player_user_id, message_id in player_messages:
        if message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=player_user_id,
                    message_id=message_id,
                    text=f"❓ <b>Вопрос {question_idx + 1}/{len(QUESTIONS)}</b> ({answered_count}/{total_players} ответили)\n\n<b>{question}</b>\n\n📝 Напиши свой ответ в чат:",
                    parse_mode='HTML'
                )
            except TelegramError as e:
                logger.error(f"Failed to update progress for {player_user_id}: {e}")
    
    if answered_count >= total_players:
        await send_question_to_players(game_id, question_idx + 1, context)

async def generate_stories(game_id, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send multiple rotated stories to all players"""
    logger.info(f"[GENERATE_STORIES] Called with game_id={game_id}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, user_id, first_name FROM game_players WHERE game_id = ?
        ORDER BY id
    ''', (game_id,))
    
    players = cursor.fetchall()
    num_players = len(players)
    logger.info(f"[GENERATE_STORIES] Found {num_players} players: {players}")
    
    cursor.execute('''
        SELECT question_idx, player_idx, answer FROM game_answers 
        WHERE game_id = ? ORDER BY question_idx, player_idx
    ''', (game_id,))
    
    all_answers = cursor.fetchall()
    logger.info(f"[GENERATE_STORIES] Found {len(all_answers)} answers")
    
    cursor.execute('''
        SELECT room_code, created_by FROM games WHERE game_id = ?
    ''', (game_id,))
    game_row = cursor.fetchone()
    if game_row:
        room_code = game_row[0]
        created_by = game_row[1]
        logger.info(f"[GENERATE_STORIES] Room code: {room_code}")
    else:
        logger.error(f"[GENERATE_STORIES] No game found for game_id={game_id}")
        conn.close()
        return
    
    # Get old player data for new game
    cursor.execute('''
        SELECT user_id, username, first_name, is_admin FROM game_players 
        WHERE game_id = ? ORDER BY joined_at
    ''', (game_id,))
    old_players = cursor.fetchall()
    
    cursor.execute('UPDATE games SET status = ? WHERE game_id = ?', ('completed', game_id))
    
    # Delete old game data and create new game with same room_code
    cursor.execute('DELETE FROM game_messages WHERE game_id = ?', (game_id,))
    cursor.execute('DELETE FROM game_answers WHERE game_id = ?', (game_id,))
    cursor.execute('DELETE FROM game_players WHERE game_id = ?', (game_id,))
    cursor.execute('DELETE FROM games WHERE game_id = ?', (game_id,))
    
    # Create new game
    cursor.execute('''
        INSERT INTO games (room_code, created_by, status, current_question_idx)
        VALUES (?, ?, ?, ?)
    ''', (room_code, created_by, 'waiting', 0))
    
    new_game_id = cursor.lastrowid
    
    # Add old players to new game
    for user_id, username, first_name, is_admin in old_players:
        cursor.execute('''
            INSERT INTO game_players (game_id, user_id, username, first_name, is_admin)
            VALUES (?, ?, ?, ?, ?)
        ''', (new_game_id, user_id, username, first_name, is_admin))
    
    conn.commit()
    conn.close()
    
    player_ids = [p[0] for p in players]
    
    all_stories = "🎉 <b>ИСТОРИИ:</b>\n\n"
    
    for story_num in range(num_players):
        story_text = build_rotated_story(all_answers, story_num, num_players, player_ids)
        all_stories += f"{story_text}\n\n"
    
    logger.info(f"[GENERATE_STORIES] Sending stories to {num_players} players")
    for player_id, user_id, first_name in players:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{all_stories}\n\nДобавляйте друзей по коду и играйте снова!",
                parse_mode='HTML'
            )
            logger.info(f"[GENERATE_STORIES] Stories sent to {first_name} (user_id={user_id})")
        except TelegramError as e:
            logger.error(f"[GENERATE_STORIES] Failed to send stories to {user_id}: {e}")
    
    # Show new room status
    logger.info(f"[GENERATE_STORIES] Calling update_room_players for new game_id={new_game_id}, room_code={room_code}")
    await update_room_players(new_game_id, room_code, context)
    logger.info(f"[GENERATE_STORIES] Completed for game_id={game_id}")

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
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("reset", reset_game))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_any_text))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
