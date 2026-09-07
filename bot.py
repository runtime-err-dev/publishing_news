import telebot
from telebot import types
import sqlite3
import datetime
import calendar
import schedule
import threading
import time
import re
import html
import os
from dotenv import load_dotenv


load_dotenv()
TOKEN = os.environ.get("TOKEN")
MAIN_ADMIN_ID = os.environ.get("MAIN_ADMIN_ID") # Впиши сюда свой Telegram ID


bot = telebot.TeleBot(TOKEN)

# ================= БАЗА ДАННЫХ =================
def get_db_connection():
    conn = sqlite3.connect('vpn_clients.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            identifier TEXT,
            nickname TEXT,
            extra_info TEXT,
            amount INTEGER,
            next_payment DATE,
            is_active INTEGER DEFAULT 1,
            awaiting_check INTEGER DEFAULT 0
        )
    ''')
    c.execute('CREATE TABLE IF NOT EXISTS admins (tg_id INTEGER PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    
    try:
        c.execute('ALTER TABLE subscriptions ADD COLUMN last_admin_reminder INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass

    c.execute('INSERT OR IGNORE INTO admins (tg_id) VALUES (?)', (MAIN_ADMIN_ID,))
    c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('morning_time', '10:00'))
    c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('evening_time', '18:00'))
    conn.commit()
    conn.close()

init_db()

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def is_admin(user_id):
    conn = get_db_connection()
    admin = conn.execute('SELECT tg_id FROM admins WHERE tg_id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(admin)

def get_all_admins():
    conn = get_db_connection()
    admins = conn.execute('SELECT tg_id FROM admins').fetchall()
    conn.close()
    return [a['tg_id'] for a in admins]

def get_main_markup(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🗒️ Мои подписки"), types.KeyboardButton("💳 Как оплатить"))
    markup.add(types.KeyboardButton("🆘 Помощь"), types.KeyboardButton("👨‍💻 О создателе"))
    if is_admin(user_id):
        markup.add(types.KeyboardButton("👑 Админ панель"))
    return markup

def get_cancel_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Отмена"))
    return markup

def add_one_month(dt):
    month, year, day = dt.month, dt.year, dt.day
    if month == 12:
        month = 1
        year += 1
    else:
        month += 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(day, max_day)
    return dt.replace(year=year, month=month, day=day)

def generate_pay_markup(sub_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid_{sub_id}"))
    return markup

def check_cancel(message):
    if message.text == "❌ Отмена":
        bot.send_message(message.chat.id, "Действие отменено.", reply_markup=get_main_markup(message.chat.id))
        return True
    return False

# ================= КЛИЕНТСКАЯ ЧАСТЬ =================
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    conn = get_db_connection()
    if username:
        conn.execute('UPDATE subscriptions SET tg_id = ? WHERE identifier = ? AND tg_id IS NULL', (user_id, username.lower()))
    conn.execute('UPDATE subscriptions SET tg_id = ? WHERE identifier = ? AND tg_id IS NULL', (user_id, str(user_id)))
    conn.commit()
    
    subs = conn.execute('SELECT * FROM subscriptions WHERE tg_id = ?', (user_id,)).fetchall()
    conn.close()

    if not subs and not is_admin(user_id):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton("📱 Поделиться контактом", request_contact=True))
        bot.send_message(user_id, "Ваш аккаунт не найден. Если вас добавили по номеру телефона, нажмите кнопку ниже, чтобы привязать аккаунт.", reply_markup=markup)
        for admin in get_all_admins():
            bot.send_message(admin, f"Человек который заинтересовался ботом: \ntg://user?id={user_id}")
        return

    bot.send_message(user_id, "Добро пожаловать, ваша подписка успешно найдена! \nЗдесь вы будете получать напоминания об оплате VPN.", reply_markup=get_main_markup(user_id))

@bot.message_handler(content_types=['contact'])
def contact_handler(message):
    if message.contact is not None:
        user_id = message.from_user.id
        phone = ''.join(filter(str.isdigit, message.contact.phone_number))
        phone_suffix = phone[-10:] if len(phone) >= 10 else phone
        
        conn = get_db_connection()
        conn.execute(f"UPDATE subscriptions SET tg_id = ? WHERE identifier LIKE '%{phone_suffix}' AND tg_id IS NULL", (user_id,))
        conn.commit()
        subs = conn.execute('SELECT * FROM subscriptions WHERE tg_id = ?', (user_id,)).fetchall()
        conn.close()
        
        if subs:
            bot.send_message(user_id, "✅ Ваш аккаунт успешно привязан по номеру телефона!", reply_markup=get_main_markup(user_id))
        else:
            bot.send_message(user_id, "❌ Подписок на этот номер не найдено. Обратитесь к администратору: @runtime_err")
            for admin in get_all_admins():
                bot.send_message(admin, f"Человек который заинтересовался ботом и отправил номер телефона: \n+{phone}\ntg://user?id={user_id}")

@bot.message_handler(func=lambda msg: msg.text == "💳 Как оплатить")
def how_to_pay(message):
    text = os.environ.get("TEXT_RECVIZITI")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "🆘 Помощь")
def help_section(message):
    text = "🛠 Возникли проблемы? Напишите администратору:\n@runtime_err"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "👨‍💻 О создателе")
def help_section(message):
    text = "Создал и разработал:\n@runtime_err\n\nРепозиторий проекта можно найти на: \nhttps://github.com/runtime-err-dev/publishing_news"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "🗒️ Мои подписки")
def my_subscriptions(message):
    user_id = message.from_user.id
    conn = get_db_connection()
    # Ищем все активные подписки, привязанные к этому Telegram ID
    subs = conn.execute('SELECT * FROM subscriptions WHERE tg_id = ? AND is_active = 1', (user_id,)).fetchall()
    conn.close()

    if not subs:
        bot.send_message(message.chat.id, "У вас пока нет активных подписок или они еще не привязаны к вашему аккаунту.")
        return

    text = "📦 <b>Ваши активные подписки:</b>\n\n"
    for c in subs:
        status = "⏳ Проверяется оплата" if c['awaiting_check'] else "✅ Активна"
        safe_extra_info = html.escape(str(c['extra_info'] or "Нет данных"))
        
        text += f"🔹 <b>{safe_extra_info}</b>\n"
        text += f"💵 Сумма: {c['amount']} руб.\n"
        text += f"📅 Следующая оплата: {c['next_payment']}\n"
        text += f"ℹ️ Статус: {status}\n〰️〰️〰️\n"
        
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def client_paid_callback(call):
    sub_id = int(call.data.split('_')[1])
    conn = get_db_connection()
    sub = conn.execute('SELECT * FROM subscriptions WHERE id = ?', (sub_id,)).fetchone()
    
    if not sub:
        bot.answer_callback_query(call.id, "Подписка не найдена.")
        return
    if sub['awaiting_check'] == 1:
        bot.answer_callback_query(call.id, "Ожидайте проверки.", show_alert=True)
        return

    current_time = int(time.time())
    conn.execute('UPDATE subscriptions SET awaiting_check = 1, last_admin_reminder = ? WHERE id = ?', (current_time, sub_id))
    conn.commit()
    conn.close()
    
    try:
        bot.edit_message_text(f"{call.message.text}\n\n⏳ *Оплата отправлена администратору. Ожидайте.*", 
                              chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    except:
        pass

    admin_markup = types.InlineKeyboardMarkup(row_width=2)
    admin_markup.add(
        types.InlineKeyboardButton("✅ Получена", callback_data=f"conf_{sub_id}"),
        types.InlineKeyboardButton("❌ Нет", callback_data=f"rej_{sub_id}")
    )
    admin_markup.add(
        types.InlineKeyboardButton("🔄 Уже зачтена ранее", callback_data=f"ignore_{sub_id}")
    )
    admin_text = f"💰 **ПРОВЕРКА ОПЛАТЫ**\nПользователь: {sub['identifier']} ({sub['nickname']})\nПодписка: {sub['extra_info']}\nСумма: {sub['amount']} руб."
    
    for admin in get_all_admins():
        try:
            bot.send_message(admin, admin_text, reply_markup=admin_markup, parse_mode="Markdown")
        except:
            pass

# ================= АДМИНСКАЯ ЧАСТЬ =================
@bot.message_handler(func=lambda msg: msg.text == "👑 Админ панель" and is_admin(msg.from_user.id))
def admin_panel(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Добавить", callback_data="admin_add_client"),
        types.InlineKeyboardButton("👥 Активные", callback_data="admin_list_clients"),
        types.InlineKeyboardButton("✏️ Изменить", callback_data="admin_edit_client"),
        types.InlineKeyboardButton("🚫 Отключить", callback_data="admin_disable_sub")
    )
    markup.add(
        types.InlineKeyboardButton("👮 Админы", callback_data="admin_add_admin"),
        types.InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")
    )
    markup.add(
        types.InlineKeyboardButton("📢 Написать всем (Рассылка)", callback_data="admin_broadcast")
    )
    bot.send_message(message.chat.id, "Панель администратора:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: is_admin(call.from_user.id))
def admin_callbacks(call):
    conn = get_db_connection()
    
    if call.data.startswith('conf_') or call.data.startswith('rej_') or call.data.startswith('ignore_'):
        sub_id = int(call.data.split('_')[1])
        sub = conn.execute('SELECT * FROM subscriptions WHERE id = ?', (sub_id,)).fetchone()
        
        if not sub:
            bot.answer_callback_query(call.id, "Подписка не найдена в базе.", show_alert=True)
            return
            
        if sub['awaiting_check'] == 0:
            bot.answer_callback_query(call.id, "Эта заявка уже была обработана!", show_alert=True)
            try:
                bot.edit_message_text(f"{call.message.text}\n\n*[УЖЕ ОБРАБОТАНО]*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
            except:
                pass
            return

        if call.data.startswith('conf_'):
            current_date = datetime.datetime.strptime(sub['next_payment'], '%Y-%m-%d').date()
            new_date = add_one_month(current_date).strftime('%Y-%m-%d')
            conn.execute('UPDATE subscriptions SET next_payment = ?, awaiting_check = 0, last_admin_reminder = 0 WHERE id = ?', (new_date, sub_id))
            try:
                bot.edit_message_text(f"{call.message.text}\n\n✅ **Подтверждено.** След: {new_date}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
            except:
                pass
            if sub['tg_id']:
                bot.send_message(sub['tg_id'], f"✅ Ваша оплата ({sub['extra_info']}) подтверждена! Следующая: {new_date}")
                
        elif call.data.startswith('rej_'):
            conn.execute('UPDATE subscriptions SET awaiting_check = 0, last_admin_reminder = 0 WHERE id = ?', (sub_id,))
            try:
                bot.edit_message_text(f"{call.message.text}\n\n❌ **Отклонено.**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
            except:
                pass
            if sub['tg_id']:
                bot.send_message(sub['tg_id'], f"❌ Администратор не подтвердил оплату ({sub['extra_info']}). Пожалуйста, проверьте платеж или свяжитесь с поддержкой: \n@runtime_err", reply_markup=generate_pay_markup(sub_id))
        elif call.data.startswith('ignore_'):
            conn.execute('UPDATE subscriptions SET awaiting_check = 0, last_admin_reminder = 0 WHERE id = ?', (sub_id,))
            try:
                bot.edit_message_text(f"{call.message.text}\n\n🔄 **Отмечено как дубль.**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
            except:
                pass
            if sub['tg_id']:
                bot.send_message(sub['tg_id'], f"🔄 Вы повторно нажали кнопку отметки об оплате ({sub['extra_info']}). Эта оплата уже была зачтена ранее, ваша следующая дата списания: {sub['next_payment']}.")
        conn.commit()

    elif call.data == "admin_list_clients":
        subs = conn.execute('SELECT * FROM subscriptions WHERE is_active = 1').fetchall()
        if not subs:
            bot.send_message(call.message.chat.id, "Активных подписок нет.")
        else:
            try:
                text = "👥 <b>Активные подписки:</b>\n\n"
                for c in subs:
                    status = "⏳ Проверяется" if c['awaiting_check'] else "Активна"
                    tg_status = "✅ Привязан" if c['tg_id'] else "❌ Не привязан"
                    safe_identifier = html.escape(str(c['identifier'] or "Нет данных"))
                    safe_nickname = html.escape(str(c['nickname'] or "Нет данных"))
                    safe_extra_info = html.escape(str(c['extra_info'] or "Нет данных"))
                    
                    chunk = f"👤 {safe_identifier} ({safe_nickname})\n"
                    chunk += f"💵 {c['amount']} руб | 📅 {c['next_payment']}\n"
                    chunk += f"ℹ️ {safe_extra_info} | {tg_status}\n"
                    chunk += f"ID: {c['id']} | Статус: {status}\n〰️〰️〰️\n"
                    
                    if len(text) + len(chunk) > 4000:
                        bot.send_message(call.message.chat.id, text, parse_mode='HTML')
                        text = ""
                    text += chunk
                if text:
                    bot.send_message(call.message.chat.id, text, parse_mode='HTML')
            except Exception as e:
                bot.send_message(call.message.chat.id, f"⚠️ Ошибка вывода списка: {e}")

    elif call.data == "admin_add_client":
        msg = bot.send_message(call.message.chat.id, "Введите ID, @username или номер телефона клиента:", reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_add_identifier)
        
    elif call.data == "admin_disable_sub":
        msg = bot.send_message(call.message.chat.id, "Введите ID подписки для отключения:", reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_disable_sub)
        
    # === ФУНКЦИИ ИЗМЕНЕНИЯ ДАННЫХ ===
    elif call.data == "admin_edit_client":
        msg = bot.send_message(call.message.chat.id, "Введите ID подписки, которую хотите изменить:", reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_edit_sub_id)

    elif call.data.startswith('editsub_'):
        parts = call.data.split('_')
        field = parts[1]
        sub_id = int(parts[2])
        
        if field == 'ident': text = "Введите новый Идентификатор (ID, @username или телефон):"
        elif field == 'nick': text = "Введите новое Имя/Прозвище:"
        elif field == 'info': text = "Введите новую доп. информацию о подписке (видна):"
        elif field == 'amount': text = "Введите новую сумму оплаты (число):"
        elif field == 'date': text = "Введите новую дату оплаты (ГГГГ-ММ-ДД):"
        
        msg = bot.send_message(call.message.chat.id, text, reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_save_edit, sub_id, field)

    elif call.data == "admin_add_admin":
        msg = bot.send_message(call.message.chat.id, "Введите Telegram ID нового администратора:", reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_add_admin)
        
    elif call.data == "admin_settings":
        morning = conn.execute("SELECT value FROM settings WHERE key='morning_time'").fetchone()['value']
        evening = conn.execute("SELECT value FROM settings WHERE key='evening_time'").fetchone()['value']
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Изменить утреннее", callback_data="set_time_morning"),
            types.InlineKeyboardButton("Изменить вечернее", callback_data="set_time_evening")
        )
        bot.send_message(call.message.chat.id, f"⚙️ Текущее время рассылки:\nУтро: {morning}\nВечер: {evening}", reply_markup=markup)
        
    elif call.data.startswith("set_time_"):
        time_type = call.data.split('_')[2]
        msg = bot.send_message(call.message.chat.id, "Введите новое время в формате ЧЧ:ММ (например 09:30):", reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_set_time, time_type)


    elif call.data == "admin_broadcast":
        msg = bot.send_message(call.message.chat.id, "Введите сообщение, которое нужно отправить всем активным клиентам:", reply_markup=get_cancel_markup())
        bot.register_next_step_handler(msg, process_broadcast_message)

    conn.close()

def process_edit_sub_id(message):
    if check_cancel(message): return
    try:
        sub_id = int(message.text)
        conn = get_db_connection()
        sub = conn.execute('SELECT * FROM subscriptions WHERE id = ?', (sub_id,)).fetchone()
        conn.close()
        
        if not sub:
            bot.send_message(message.chat.id, "Подписка с таким ID не найдена.", reply_markup=get_main_markup(message.chat.id))
            return
            
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("Идентификатор", callback_data=f"editsub_ident_{sub_id}"),
            types.InlineKeyboardButton("Имя/Прозвище", callback_data=f"editsub_nick_{sub_id}"),
            types.InlineKeyboardButton("Доп. инфо", callback_data=f"editsub_info_{sub_id}"),
            types.InlineKeyboardButton("Сумма", callback_data=f"editsub_amount_{sub_id}"),
            types.InlineKeyboardButton("Дата оплаты", callback_data=f"editsub_date_{sub_id}")
        )
        
        safe_identifier = html.escape(str(sub['identifier']))
        safe_nickname = html.escape(str(sub['nickname']))
        
        text = f"⚙️ <b>Редактирование подписки #{sub_id}</b>\n\n"
        text += f"👤 {safe_identifier} ({safe_nickname})\n"
        text += f"ℹ️ {html.escape(str(sub['extra_info']))}\n"
        text += f"💵 {sub['amount']} руб | 📅 {sub['next_payment']}\n\n"
        text += "Что хотите изменить?"
        
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    except ValueError:
        bot.send_message(message.chat.id, "ID должен быть числом.", reply_markup=get_main_markup(message.chat.id))

def process_save_edit(message, sub_id, field):
    if check_cancel(message): return
    new_val = message.text
    conn = get_db_connection()
    try:
        if field == 'ident':
            new_val = new_val.replace('@', '').strip().lower()
            conn.execute('UPDATE subscriptions SET identifier = ? WHERE id = ?', (new_val, sub_id))
        elif field == 'nick':
            conn.execute('UPDATE subscriptions SET nickname = ? WHERE id = ?', (new_val, sub_id))
        elif field == 'info':
            conn.execute('UPDATE subscriptions SET extra_info = ? WHERE id = ?', (new_val, sub_id))
        elif field == 'amount':
            val = int(new_val)
            conn.execute('UPDATE subscriptions SET amount = ? WHERE id = ?', (val, sub_id))
        elif field == 'date':
            datetime.datetime.strptime(new_val, '%Y-%m-%d') # Проверка формата
            conn.execute('UPDATE subscriptions SET next_payment = ? WHERE id = ?', (new_val, sub_id))
            
        conn.commit()
        bot.send_message(message.chat.id, "✅ Данные успешно обновлены!", reply_markup=get_main_markup(message.chat.id))
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка формата! (Сумма должна быть числом, а дата в формате ГГГГ-ММ-ДД).", reply_markup=get_main_markup(message.chat.id))
    finally:
        conn.close()

# --- Настройка времени ---
def process_set_time(message, time_type):
    if check_cancel(message): return
    if re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", message.text):
        conn = get_db_connection()
        key = 'morning_time' if time_type == 'morning' else 'evening_time'
        conn.execute('UPDATE settings SET value = ? WHERE key = ?', (message.text, key))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"✅ Время успешно изменено на {message.text}.", reply_markup=get_main_markup(message.chat.id))
        reload_scheduler()
    else:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуйте еще раз в формате ЧЧ:ММ.", reply_markup=get_main_markup(message.chat.id))


def process_broadcast_message(message):
    if check_cancel(message): return
    
    conn = get_db_connection()
    # Выбираем только уникальные tg_id
    subs = conn.execute('SELECT DISTINCT tg_id FROM subscriptions WHERE is_active = 1 AND tg_id IS NOT NULL').fetchall()
    conn.close()
    
    success_count = 0
    bot.send_message(message.chat.id, "⏳ Начинаю рассылку...")
    
    for sub in subs:
        try:
            bot.copy_message(chat_id=sub['tg_id'], from_chat_id=message.chat.id, message_id=message.message_id, reply_markup=get_main_markup(123))
            success_count += 1
        except Exception:
            # Игнорируем ошибку, если пользователь заблокировал бота
            pass 
            
    bot.send_message(message.chat.id, f"✅ Рассылка завершена!\nСообщение доставлено: {success_count} пользователям.", reply_markup=get_main_markup(message.chat.id))
def process_add_identifier(message):
    if check_cancel(message): return
    identifier = message.text.replace('@', '').strip().lower()
    msg = bot.send_message(message.chat.id, "Введите сумму ежемесячной оплаты (число):")
    bot.register_next_step_handler(msg, process_add_amount, {'identifier': identifier})

def process_add_amount(message, user_data):
    if check_cancel(message): return
    try:
        user_data['amount'] = int(message.text)
        msg = bot.send_message(message.chat.id, "Введите дату следующей оплаты (ГГГГ-ММ-ДД):")
        bot.register_next_step_handler(msg, process_add_date, user_data)
    except ValueError:
        bot.send_message(message.chat.id, "Ошибка! Нужно число.", reply_markup=get_main_markup(message.chat.id))

def process_add_date(message, user_data):
    if check_cancel(message): return
    try:
        datetime.datetime.strptime(message.text, '%Y-%m-%d')
        user_data['date'] = message.text
        msg = bot.send_message(message.chat.id, "Введите имя/прозвище клиента:")
        bot.register_next_step_handler(msg, process_add_nickname, user_data)
    except ValueError:
        bot.send_message(message.chat.id, "Ошибка! Неверный формат даты.", reply_markup=get_main_markup(message.chat.id))

def process_add_nickname(message, user_data):
    if check_cancel(message): return
    user_data['nickname'] = message.text
    msg = bot.send_message(message.chat.id, "Введите дополнительную информацию о подписке (видна):")
    bot.register_next_step_handler(msg, process_add_extrainfo, user_data)

def process_add_extrainfo(message, user_data):
    if check_cancel(message): return
    user_data['extra_info'] = message.text
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO subscriptions (identifier, nickname, extra_info, amount, next_payment)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_data['identifier'], user_data['nickname'], user_data['extra_info'], user_data['amount'], user_data['date']))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ Подписка для {user_data['identifier']} добавлена!\nПопросите клиента запустить бота.", reply_markup=get_main_markup(message.chat.id))

def process_disable_sub(message):
    if check_cancel(message): return
    try:
        sub_id = int(message.text)
        conn = get_db_connection()
        conn.execute('UPDATE subscriptions SET is_active = 0 WHERE id = ?', (sub_id,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"✅ Подписка #{sub_id} отключена.", reply_markup=get_main_markup(message.chat.id))
    except ValueError:
        bot.send_message(message.chat.id, "ID должен быть числом.", reply_markup=get_main_markup(message.chat.id))

def process_add_admin(message):
    if check_cancel(message): return
    try:
        new_admin = int(message.text)
        conn = get_db_connection()
        conn.execute('INSERT OR IGNORE INTO admins (tg_id) VALUES (?)', (new_admin,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, "✅ Администратор добавлен.", reply_markup=get_main_markup(message.chat.id))
    except ValueError:
        bot.send_message(message.chat.id, "ID должен быть числом.", reply_markup=get_main_markup(message.chat.id))

# ================= СИСТЕМА УВЕДОМЛЕНИЙ (SCHEDULER) =================
def check_payments_daily():
    conn = get_db_connection()
    subs = conn.execute('SELECT * FROM subscriptions WHERE is_active = 1 AND awaiting_check = 0 AND tg_id IS NOT NULL').fetchall()
    conn.close()
    
    today = datetime.date.today()
    admins = get_all_admins()

    for c in subs:
        pay_date = datetime.datetime.strptime(c['next_payment'], '%Y-%m-%d').date()
        diff = (pay_date - today).days
        markup = generate_pay_markup(c['id'])

        try:
            if diff == 3:
                bot.send_message(c['tg_id'], f"⚠️ Через 3 дня нужно продлить VPN ({c['extra_info']}), оплати подписку что-бы продолжить пользоваться!.\nСумма: {c['amount']} руб.", reply_markup=markup)
            elif diff == 1:
                bot.send_message(c['tg_id'], f"⚠️ Завтра подписка на VPN ({c['extra_info']}) закончится, оплати что-бы продолжить пользоваться!\nСумма: {c['amount']} руб.", reply_markup=markup)
            elif diff == 0:
                bot.send_message(c['tg_id'], f"🚨 Сегодня подписка на VPN ({c['extra_info']}) закончилась, оплати что-бы продолжить пользоваться!\nСумма: {c['amount']} руб.", reply_markup=markup)
                for admin in admins:
                    bot.send_message(admin, f"💰 Сегодня день оплаты!\nПользователь: {c['identifier']} ({c['nickname']})\nПодписка: {c['extra_info']}")
            elif diff < 0:
                bot.send_message(c['tg_id'], f"❌ Просрочена оплата VPN ({c['extra_info']}) на {abs(diff)} дней! \nОплати что-бы продолжить пользоваться! \nК оплате: {c['amount']} руб.", reply_markup=markup)
                for admin in admins:
                    bot.send_message(admin, f"❌ ПРОСРОЧКА:\nПользователь: {c['identifier']} ({c['nickname']})\nДолг: {c['amount']} руб.")
        except:
            pass

def check_overdue_evening():
    conn = get_db_connection()
    subs = conn.execute('SELECT * FROM subscriptions WHERE is_active = 1 AND awaiting_check = 0 AND tg_id IS NOT NULL').fetchall()
    conn.close()
    today = datetime.date.today()
    for c in subs:
        pay_date = datetime.datetime.strptime(c['next_payment'], '%Y-%m-%d').date()
        if (pay_date - today).days < 0:
            try:
                bot.send_message(c['tg_id'], f"❌ Повторное напоминание: У вас просрочена оплата VPN ({c['extra_info']})!\nСумма: {c['amount']} руб.", reply_markup=generate_pay_markup(c['id']))
            except:
                pass

def check_forgotten_admin_confirmations():
    conn = get_db_connection()
    pending_subs = conn.execute('SELECT * FROM subscriptions WHERE awaiting_check = 1').fetchall()
    current_time = int(time.time())
    admins = get_all_admins()
    
    for sub in pending_subs:
        if current_time - sub['last_admin_reminder'] >= 3500:
            admin_markup = types.InlineKeyboardMarkup(row_width=2)
            admin_markup.add(
                types.InlineKeyboardButton("✅ Получена", callback_data=f"conf_{sub['id']}"),
                types.InlineKeyboardButton("❌ Нет", callback_data=f"rej_{sub['id']}")
            )
            admin_markup.add(
                types.InlineKeyboardButton("🔄 Уже зачтена ранее", callback_data=f"ignore_{sub['id']}")
            )
            admin_text = f"⚠️ **ПОВТОРНОЕ НАПОМИНАНИЕ** ⚠️\n\nОжидает проверки оплаты!\nПользователь: {sub['identifier']} ({sub['nickname']})\nПодписка: {sub['extra_info']}\nСумма: {sub['amount']} руб."
            
            for admin in admins:
                try:
                    bot.send_message(admin, admin_text, reply_markup=admin_markup, parse_mode="Markdown")
                except:
                    pass
            conn.execute('UPDATE subscriptions SET last_admin_reminder = ? WHERE id = ?', (current_time, sub['id']))
            
    conn.commit()
    conn.close()

def get_schedule_times():
    conn = get_db_connection()
    m = conn.execute("SELECT value FROM settings WHERE key='morning_time'").fetchone()['value']
    e = conn.execute("SELECT value FROM settings WHERE key='evening_time'").fetchone()['value']
    conn.close()
    return m, e

def reload_scheduler():
    schedule.clear()
    m, e = get_schedule_times()
    schedule.every().day.at(m).do(check_payments_daily)
    schedule.every().day.at(e).do(check_overdue_evening)
    schedule.every(10).minutes.do(check_forgotten_admin_confirmations)
    print(f"Расписание обновлено: утро {m}, вечер {e}, проверки оплат - каждые 10 мин.")

def run_scheduler():
    reload_scheduler()
    while True:
        schedule.run_pending()
        time.sleep(30)

# ================= ЗАПУСК =================
if __name__ == '__main__':
    threading.Thread(target=run_scheduler, daemon=True).start()
    print("Бот запущен...")
    bot.infinity_polling()