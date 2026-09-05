import os
import telebot
from telebot import types
import psycopg2
from psycopg2.extras import RealDictCursor

# Cấu hình từ Biến môi trường
TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL', 'postgres://user:password@localhost:5432/dbname')
ADMIN_ID = 7907990385
REQUIRED_GROUP = "@nhomsharemodallgame"

bot = telebot.TeleBot(TOKEN)

# --- KẾT NỐI VÀ KHỞI TẠO DATABASE ---
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Bảng User (Lưu ref bằng danh sách ID trong referred_ids)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            balance INT DEFAULT 0,
            verified BOOLEAN DEFAULT FALSE,
            referred_by BIGINT DEFAULT NULL,
            referred_ids TEXT DEFAULT '',
            ref_xu INT DEFAULT 0
        )
    ''')
    # Bảng Kho Acc Demo (Toàn bộ là tài khoản giả)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY,
            category VARCHAR(50),
            account_data TEXT,
            sold BOOLEAN DEFAULT FALSE
        )
    ''')
    # Bảng Lịch sử mua hàng
    cur.execute('''
        CREATE TABLE IF NOT EXISTS purchase_history (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            category VARCHAR(50),
            account_data TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

# --- KIỂM TRA THÀNH VIÊN NHÓM ---
def check_user_membership(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_GROUP, user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
    except Exception as e:
        print(f"Lỗi kiểm tra nhóm: {e}")
    return False

# --- XỬ LÝ /START & XÁC MINH ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    args = message.text.split()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,))
    user = cur.fetchone()
    
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        ref_potential = int(args[1])
        if ref_potential != user_id:
            referrer_id = ref_potential

    if not user:
        cur.execute(
            "INSERT INTO users (telegram_id, referred_by) VALUES (%s, %s)",
            (user_id, referrer_id)
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,))
        user = cur.fetchone()

    cur.close()
    conn.close()

    if not user['verified']:
        if check_user_membership(user_id):
            verify_user(user_id)
            show_main_menu(message.chat.id)
        else:
            send_verification_prompt(message.chat.id)
    else:
        show_main_menu(message.chat.id)

def verify_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET verified = TRUE WHERE telegram_id = %s AND verified = FALSE", (user_id,))
    
    cur.execute("SELECT referred_by FROM users WHERE telegram_id = %s", (user_id,))
    res = cur.fetchone()
    
    if res and res['referred_by']:
        ref_id = res['referred_by']
        cur.execute("SELECT referred_ids FROM users WHERE telegram_id = %s", (ref_id,))
        ref_user = cur.fetchone()
        
        if ref_user:
            current_ids = ref_user['referred_ids'] or ""
            id_list = current_ids.split(',') if current_ids else []
            
            # Chống ref trùng: Chỉ tính khi ID chưa có trong danh sách
            if str(user_id) not in id_list:
                id_list.append(str(user_id))
                new_ids_str = ','.join(id_list)
                
                cur.execute(
                    "UPDATE users SET balance = balance + 2, referred_ids = %s, ref_xu = ref_xu + 2 WHERE telegram_id = %s",
                    (new_ids_str, ref_id)
                )
                try:
                    bot.send_message(ref_id, f"🎉 Bạn nhận được **2 Xu** từ người được giới thiệu có ID `{user_id}` đã xác minh nhóm thành công!", parse_mode="Markdown")
                except:
                    pass

    conn.commit()
    cur.close()
    conn.close()

def send_verification_prompt(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Tham gia nhóm", url=f"https://t.me/{REQUIRED_GROUP.replace('@','')}"))
    markup.add(types.InlineKeyboardButton("✅ Tôi đã tham gia", callback_data="check_membership"))
    
    text = (
        "⚠️ **BẠN CHƯA HOÀN TẤT XÁC MINH**\n\n"
        f"Để sử dụng bot, bạn bắt buộc phải tham gia nhóm {REQUIRED_GROUP}.\n"
        "Sau khi tham gia, hãy bấm nút **'Tôi đã tham gia'** bên dưới."
    )
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "check_membership")
def callback_check_membership(call):
    user_id = call.from_user.id
    if check_user_membership(user_id):
        verify_user(user_id)
        bot.answer_callback_query(call.id, "Xác minh thành công!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_main_menu(call.message.chat.id)
    else:
        bot.answer_callback_query(call.id, "❌ Bạn vẫn chưa tham gia nhóm!", show_alert=True)

# --- MENU CHÍNH ---
def show_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🔥 SHOP ACC FREE FIRE 🔥", "👤 Tài khoản")
    markup.add("🎁 Referral", "🔄 Làm mới menu")
    bot.send_message(chat_id, "✨ **CHÀO MỪNG ĐẾN VỚI HỆ THỐNG AXIOM** ✨\nVui lòng chọn chức năng bên dưới:", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "🔄 Làm mới menu")
def menu_refresh(message):
    show_main_menu(message.chat.id)

# --- SHOP ACC FREE FIRE (DEMO) ---
@bot.message_handler(func=lambda message: message.text == "🔥 SHOP ACC FREE FIRE 🔥")
def shop_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Clone Level 5–8 (10 Xu)", callback_data="shop_view_level_5_8"),
        types.InlineKeyboardButton("📦 Clone Level 30 (15 Xu)", callback_data="shop_view_level_30")
    )
    bot.send_message(message.chat.id, "🔥 **DANH MỤC SHOP ACC FREE FIRE (100% TÀI KHOẢN DEMO/GIẢ)**\nChọn loại tài khoản bạn muốn xem:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_view_"))
def callback_shop_view(call):
    category = call.data.replace("shop_view_", "")
    cat_name = "Clone Level 5–8" if category == "level_5_8" else "Clone Level 30"
    price = 10 if category == "level_5_8" else 15
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM accounts WHERE category = %s AND sold = FALSE", (category,))
    count = cur.fetchone()['count']
    cur.close()
    conn.close()

    markup = types.InlineKeyboardMarkup()
    if count > 0:
        markup.add(types.InlineKeyboardButton(f"🛒 Mua ngay DEMO ({price} Xu)", callback_data=f"buy_{category}"))
    markup.add(types.InlineKeyboardButton("⬅️ Quay lại shop", callback_data="back_shop"))

    text = f"📦 **Loại tài khoản:** {cat_name}\n💰 **Giá:** {price} Xu\n📊 **Còn lại trong kho (DEMO):** {count} acc\n\n*(Lưu ý: Toàn bộ tài khoản ở đây đều là giả/DEMO)*"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "back_shop")
def callback_back_shop(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Clone Level 5–8 (10 Xu)", callback_data="shop_view_level_5_8"),
        types.InlineKeyboardButton("📦 Clone Level 30 (15 Xu)", callback_data="shop_view_level_30")
    )
    bot.edit_message_text("🔥 **DANH MỤC SHOP ACC FREE FIRE (100% TÀI KHOẢN DEMO/GIẢ)**\nChọn loại tài khoản bạn muốn xem:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def callback_buy(call):
    category = call.data.replace("buy_", "")
    user_id = call.from_user.id
    price = 10 if category == "level_5_8" else 15

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT balance FROM users WHERE telegram_id = %s", (user_id,))
    user = cur.fetchone()
    if not user or user['balance'] < price:
        bot.answer_callback_query(call.id, "❌ Bạn không đủ Xu để mua tài khoản DEMO này!", show_alert=True)
        cur.close()
        conn.close()
        return

    # Chống bán trùng: Lấy 1 acc chưa bán và khóa dòng dữ liệu lại
    cur.execute("SELECT id, account_data FROM accounts WHERE category = %s AND sold = FALSE LIMIT 1 FOR UPDATE", (category,))
    acc = cur.fetchone()
    if not acc:
        bot.answer_callback_query(call.id, "❌ Kho tài khoản DEMO đã hết hàng!", show_alert=True)
        cur.close()
        conn.close()
        return

    acc_id = acc['id']
    acc_data = acc['account_data']

    cur.execute("UPDATE users SET balance = balance - %s WHERE telegram_id = %s", (price, user_id))
    cur.execute("UPDATE accounts SET sold = TRUE WHERE id = %s", (acc_id,))
    cur.execute("INSERT INTO purchase_history (telegram_id, category, account_data) VALUES (%s, %s, %s)", (user_id, category, acc_data))
    
    conn.commit()
    cur.close()
    conn.close()

    bot.answer_callback_query(call.id, "✅ Giao dịch DEMO thành công!")
    bot.send_message(call.message.chat.id, f"🎉 **GIAO DỊCH THÀNH CÔNG!**\n\n📦 **Tài khoản DEMO (giả) của bạn:**\n`{acc_data}`\n\n*(Cảm ơn bạn đã trải nghiệm hệ thống)*", parse_mode="Markdown")

# --- TÀI KHOẢN ---
@bot.message_handler(func=lambda message: message.text == "👤 Tài khoản")
def account_info(message):
    user_id = message.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        bot.send_message(message.chat.id, "Vui lòng bấm /start để khởi tạo tài khoản.")
        return

    current_ids = user['referred_ids'] or ""
    ref_count = len(current_ids.split(',')) if current_ids else 0

    text = (
        f"👤 **THÔNG TIN TÀI KHOẢN**\n\n"
        f"🆔 **Telegram ID:** `{user['telegram_id']}`\n"
        f"💰 **Số Xu hiện tại:** `{user['balance']} Xu`\n"
        f"👥 **Số lượt giới thiệu:** `{ref_count} người`\n"
        f"🎁 **Tổng Xu từ ref:** `{user['ref_xu']} Xu`"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# --- REFERRAL ---
@bot.message_handler(func=lambda message: message.text == "🎁 Referral")
def referral_info(message):
    user_id = message.from_user.id
    bot_info = bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT referred_ids, ref_xu FROM users WHERE telegram_id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    current_ids = user['referred_ids'] or ""
    ref_count = len(current_ids.split(',')) if current_ids else 0

    text = (
        f"🎁 **HỆ THỐNG GIỚI THIỆU (REFERRAL)**\n\n"
        f"🔗 **Link ref cá nhân của bạn:**\n`{ref_link}`\n\n"
        f"👥 **Số người đã giới thiệu thành công:** `{ref_count}`\n"
        f"💰 **Tổng Xu nhận được từ ref:** `{user['ref_xu']} Xu`\n\n"
        f"📌 **Thể lệ:** Người được giới thiệu phải bấm vào link và xác minh tham gia nhóm {REQUIRED_GROUP} bạn mới nhận được **+2 Xu**."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        return
    text = (
        "👑 **BẢNG ĐIỀU KHIỂN ADMIN**\n\n"
        "Các lệnh hỗ trợ:\n"
        "• `/addxu <user_id> <số_xu>` - Cộng xu cho user\n"
        "• `/themkho <loại> <email|pass>` - Thêm acc DEMO (level_5_8 hoặc level_30)\n"
        "• `/thongke` - Xem thống kê toàn hệ thống\n"
        "• `/thongbao <nội_dung>` - Broadcast thông báo"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['addxu'])
def admin_add_xu(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        bot.send_message(message.chat.id, "Sai cú pháp! Dùng: `/addxu <user_id> <số_xu>`", parse_mode="Markdown")
        return
    
    target_id = int(args[1])
    amount = int(args[2])

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + %s WHERE telegram_id = %s", (amount, target_id))
    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, f"✅ Đã cộng {amount} Xu cho user `{target_id}`.", parse_mode="Markdown")
    try:
        bot.send_message(target_id, f"🎉 Bạn vừa được Admin cộng **{amount} Xu** vào tài khoản!", parse_mode="Markdown")
    except:
        pass

@bot.message_handler(commands=['themkho'])
def admin_add_stock(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        bot.send_message(message.chat.id, "Sai cú pháp! Dùng: `/themkho <level_5_8/level_30> <email|password>`", parse_mode="Markdown")
        return

    category = args[1]
    if category not in ['level_5_8', 'level_30']:
        bot.send_message(message.chat.id, "❌ Loại kho không hợp lệ! Chỉ nhận `level_5_8` hoặc `level_30`.", parse_mode="Markdown")
        return

    accounts_input = args[2].split('\n')
    conn = get_db_connection()
    cur = conn.cursor()
    added_count = 0

    for line in accounts_input:
        line = line.strip()
        if '|' in line:
            cur.execute("INSERT INTO accounts (category, account_data, sold) VALUES (%s, %s, FALSE)", (category, line))
            added_count += 1

    # Lấy lại số lượng tồn kho mới nhất sau khi thêm để hiển thị cho Admin
    cur.execute("SELECT COUNT(*) FROM accounts WHERE category = 'level_5_8' AND sold = FALSE")
    stock_5_8 = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) FROM accounts WHERE category = 'level_30' AND sold = FALSE")
    stock_30 = cur.fetchone()['count']

    conn.commit()
    cur.close()
    conn.close()

    # Hiển thị phản hồi kèm toàn bộ menu quản trị và kho acc demo hiện tại theo yêu cầu
    response_text = (
        f"✅ **THÊM KHO DEMO THÀNH CÔNG!**\n"
        f"- Đã thêm: `{added_count}` acc vào loại `{category}`\n\n"
        f"📦 **KHO TÀI KHOẢN DEMO HIỆN TẠI:**\n"
        f"• Kho Clone Level 5–8: `{stock_5_8}` acc\n"
        f"• Kho Clone Level 30: `{stock_30}` acc\n\n"
        f"👑 **MENU QUẢN TRỊ ADMIN:**\n"
        f"• `/addxu <user_id> <số_xu>` - Cộng Xu\n"
        f"• `/themkho <loại> <acc>` - Thêm acc DEMO\n"
        f"• `/thongke` - Xem thống kê hệ thống\n"
        f"• `/thongbao <nội_dung>` - Gửi thông báo"
    )
    bot.send_message(message.chat.id, response_text, parse_mode="Markdown")

@bot.message_handler(commands=['thongke'])
def admin_stats(message):
    if message.from_user.id != ADMIN_ID:
        return

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) FROM users WHERE verified = TRUE")
    verified_users = cur.fetchone()['count']

    # Tính tổng lượt ref từ chuỗi referred_ids của tất cả user
    cur.execute("SELECT referred_ids FROM users")
    all_refs = cur.fetchall()
    total_refs = sum(len(r['referred_ids'].split(',')) for r in all_refs if r['referred_ids'])

    cur.execute("SELECT SUM(ref_xu) FROM users")
    total_ref_xu = cur.fetchone()['sum'] or 0

    cur.execute("SELECT SUM(balance) FROM users")
    circulating_xu = cur.fetchone()['sum'] or 0

    cur.execute("SELECT COUNT(*) FROM purchase_history")
    total_purchases = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) FROM accounts WHERE category = 'level_5_8' AND sold = FALSE")
    stock_5_8 = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) FROM accounts WHERE category = 'level_30' AND sold = FALSE")
    stock_30 = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) FROM accounts WHERE sold = TRUE")
    total_sold = cur.fetchone()['count']

    cur.close()
    conn.close()

    text = (
        f"📊 **THỐNG KÊ HỆ THỐNG AXIOM (DEMO)**\n\n"
        f"• Tổng user: `{total_users}`\n"
        f"• User đã xác minh: `{verified_users}`\n"
        f"• Tổng lượt ref: `{total_refs}`\n"
        f"• Tổng Xu từ ref: `{total_ref_xu}` Xu\n"
        f"• Tổng Xu đang lưu hành: `{circulating_xu}` Xu\n"
        f"• Tổng lượt mua DEMO: `{total_purchases}`\n"
        f"• Kho Level 5–8 còn: `{stock_5_8}` acc\n"
        f"• Kho Level 30 còn: `{stock_30}` acc\n"
        f"• Số acc DEMO đã bán: `{total_sold}` acc"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['thongbao'])
def admin_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    text_to_send = message.text.replace('/thongbao', '').strip()
    if not text_to_send:
        bot.send_message(message.chat.id, "Vui lòng nhập nội dung thông báo sau lệnh `/thongbao`.")
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users")
    users = cur.fetchall()
    cur.close()
    conn.close()

    success = 0
    failed = 0

    for u in users:
        try:
            bot.send_message(u['telegram_id'], f"📢 **THÔNG BÁO TỪ HỆ THỐNG**\n\n{text_to_send}", parse_mode="Markdown")
            success += 1
        except:
            failed += 1

    bot.send_message(message.chat.id, f"✅ Gửi broadcast hoàn tất!\n- Thành công: `{success}`\n- Thất bại: `{failed}`", parse_mode="Markdown")

# --- CHẠY BOT ---
if __name__ == "__main__":
    print("Axiom Bot (Demo) đang chạy...")
    bot.infinity_polling()
