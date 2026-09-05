import os
import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone

import asyncpg
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = "8483501766:AAEQzYZG1iX5bO0y46pWCesqKmlWucKoxlg"
DATABASE_URL = "postgresql://freefire_user:cFLd2EMJJAJRtmRRcHLiXxAJTDQbMzC8@dpg-dae7a4gn74is73cm2n80-a/freefire_demo"

ADMIN_ID = 7907990385

GROUP_USERNAME = "@nhomsharemodallgame"
GROUP_LINK = "https://t.me/nhomsharemodallgame"

REF_REWARD = 2

PRODUCTS = {
    "clone_5_8": {
        "name": "🟢 Clone Level 5–8",
        "price": 10,
    },
    "clone_30": {
        "name": "🔵 Clone Level 30",
        "price": 15,
    },
}

if not BOT_TOKEN:
    raise RuntimeError("Thiếu BOT_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("Thiếu DATABASE_URL")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("free-fire-demo-shop")


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()

db_pool: asyncpg.Pool | None = None


# ============================================================
# FSM
# ============================================================

class AddStockState(StatesGroup):
    choose_product = State()
    enter_quantity = State()
    enter_accounts = State()


class AddXuState(StatesGroup):
    enter_user_id = State()
    enter_amount = State()


class BroadcastState(StatesGroup):
    waiting_content = State()


# ============================================================
# DATABASE INIT
# ============================================================

async def init_db():
    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        command_timeout=30,
    )

    async with db_pool.acquire() as conn:

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,

                balance INTEGER NOT NULL DEFAULT 0,

                referrer_id BIGINT,
                pending_referrer_id BIGINT,

                ref_count INTEGER NOT NULL DEFAULT 0,
                ref_earned INTEGER NOT NULL DEFAULT 0,

                verified BOOLEAN NOT NULL DEFAULT FALSE,
                started BOOLEAN NOT NULL DEFAULT TRUE,

                total_purchases INTEGER NOT NULL DEFAULT 0,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referred_id BIGINT PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                reward INTEGER NOT NULL DEFAULT 2,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS demo_accounts (
                id BIGSERIAL PRIMARY KEY,

                product TEXT NOT NULL,

                email TEXT NOT NULL,
                password TEXT NOT NULL,

                sold BOOLEAN NOT NULL DEFAULT FALSE,
                sold_to BIGINT,
                sold_at TIMESTAMPTZ,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id BIGSERIAL PRIMARY KEY,

                user_id BIGINT NOT NULL,
                account_id BIGINT NOT NULL,

                product TEXT NOT NULL,
                price INTEGER NOT NULL,

                email TEXT NOT NULL,
                password TEXT NOT NULL,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id BIGSERIAL PRIMARY KEY,

                admin_id BIGINT NOT NULL,
                content TEXT NOT NULL,

                success INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_demo_accounts_product_sold
            ON demo_accounts(product, sold);
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_verified
            ON users(verified);
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_purchases_user
            ON purchases(user_id);
        """)

    logger.info("PostgreSQL initialized successfully")


# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def ensure_user(message: Message):
    user = message.from_user

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (
                user_id,
                username,
                first_name,
                started
            )
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (user_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                started = TRUE,
                updated_at = NOW()
        """,
            user.id,
            user.username,
            user.first_name,
        )


async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1",
            user_id,
        )


async def get_stock(product: str) -> int:
    async with db_pool.acquire() as conn:
        return await conn.fetchval("""
            SELECT COUNT(*)
            FROM demo_accounts
            WHERE product = $1
              AND sold = FALSE
        """, product)


async def get_all_stock():
    return {
        product: await get_stock(product)
        for product in PRODUCTS
    }


# ============================================================
# REF SYSTEM
# ============================================================

async def save_pending_ref(user_id: int, referrer_id: int):
    if user_id == referrer_id:
        return False

    async with db_pool.acquire() as conn:

        exists = await conn.fetchval("""
            SELECT 1
            FROM referrals
            WHERE referred_id = $1
        """, user_id)

        if exists:
            return False

        target = await conn.fetchval("""
            SELECT user_id
            FROM users
            WHERE user_id = $1
        """, referrer_id)

        if not target:
            return False

        await conn.execute("""
            UPDATE users
            SET pending_referrer_id = $2,
                updated_at = NOW()
            WHERE user_id = $1
              AND referrer_id IS NULL
        """,
            user_id,
            referrer_id,
        )

        return True


async def confirm_referral_after_verify(user_id: int):
    async with db_pool.acquire() as conn:
        async with conn.transaction():

            user = await conn.fetchrow("""
                SELECT
                    user_id,
                    pending_referrer_id,
                    referrer_id
                FROM users
                WHERE user_id = $1
                FOR UPDATE
            """, user_id)

            if not user:
                return False

            if user["referrer_id"] is not None:
                return False

            referrer_id = user["pending_referrer_id"]

            if not referrer_id:
                return False

            if referrer_id == user_id:
                return False

            already = await conn.fetchval("""
                SELECT 1
                FROM referrals
                WHERE referred_id = $1
            """, user_id)

            if already:
                return False

            referrer_exists = await conn.fetchval("""
                SELECT 1
                FROM users
                WHERE user_id = $1
                FOR UPDATE
            """, referrer_id)

            if not referrer_exists:
                return False

            await conn.execute("""
                INSERT INTO referrals (
                    referred_id,
                    referrer_id,
                    reward
                )
                VALUES ($1, $2, $3)
            """,
                user_id,
                referrer_id,
                REF_REWARD,
            )

            await conn.execute("""
                UPDATE users
                SET
                    referrer_id = $2,
                    pending_referrer_id = NULL
                WHERE user_id = $1
            """,
                user_id,
                referrer_id,
            )

            await conn.execute("""
                UPDATE users
                SET
                    balance = balance + $2,
                    ref_count = ref_count + 1,
                    ref_earned = ref_earned + $2,
                    updated_at = NOW()
                WHERE user_id = $1
            """,
                referrer_id,
                REF_REWARD,
            )

            return True


# ============================================================
# GROUP VERIFY
# ============================================================

async def is_member(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(
            GROUP_USERNAME,
            user_id,
        )

        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }

    except Exception as e:
        logger.warning(
            "Membership check failed for %s: %s",
            user_id,
            e,
        )
        return False


def join_keyboard():
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📢 Tham gia nhóm",
            url=GROUP_LINK,
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="✅ Tôi đã tham gia",
            callback_data="verify_join",
        )
    )

    return builder.as_markup()


# ============================================================
# SHOP UI
# ============================================================

async def shop_text(user_id: int):
    user = await get_user(user_id)
    stock = await get_all_stock()

    username = user["username"]

    if username:
        display_name = f"@{username}"
    else:
        display_name = user["first_name"] or str(user_id)

    return f"""
<b>╭━━━━━━━━━━━━━━━━━━━━╮</b>
<b>     🔥 SHOP ACC FREE FIRE 🔥</b>
<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>

👤 Xin chào: <b>{display_name}</b>

💰 Số dư: <b>{user["balance"]} Xu</b>
👥 Ref: <b>{user["ref_count"]} người</b>

<b>📦 KHO HIỆN TẠI</b>

🟢 Clone Level 5–8:
<b>{stock["clone_5_8"]} acc</b>

🔵 Clone Level 30:
<b>{stock["clone_30"]} acc</b>

⚠️ <b>Tất cả tài khoản trong bot là DỮ LIỆU DEMO.</b>

👇 Chọn chức năng:
"""


def shop_keyboard():
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🟢 Clone Level 5–8 • 10 Xu",
            callback_data="buy_clone_5_8",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔵 Clone Level 30 • 15 Xu",
            callback_data="buy_clone_30",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="💰 Kiếm Xu",
            callback_data="earn_xu",
        ),
        InlineKeyboardButton(
            text="🔗 Ref của tôi",
            callback_data="my_ref",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="👤 Tài khoản",
            callback_data="account",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Làm mới",
            callback_data="refresh_shop",
        )
    )

    return builder.as_markup()


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    await ensure_user(message)

    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    # --------------------------------------------------------
    # REF LINK
    # --------------------------------------------------------

    if len(args) > 1:
        payload = args[1].strip()

        if payload.startswith("ref_"):
            try:
                referrer_id = int(payload[4:])

                await save_pending_ref(
                    user_id,
                    referrer_id,
                )

            except ValueError:
                pass

    user = await get_user(user_id)

    # --------------------------------------------------------
    # ĐÃ VERIFY → VÀO SHOP THẲNG
    # --------------------------------------------------------

    if user["verified"]:
        await message.answer(
            await shop_text(user_id),
            reply_markup=shop_keyboard(),
        )
        return

    # --------------------------------------------------------
    # CHƯA VERIFY
    # --------------------------------------------------------

    await message.answer(
        """
<b>🔥 SHOP ACC FREE FIRE 🔥</b>

👋 Chào mừng bạn đến với shop.

🔐 Để sử dụng bot, bạn cần tham gia nhóm của chúng tôi.

Sau khi tham gia, bấm:
<b>✅ Tôi đã tham gia</b>

⚠️ Đây là hệ thống <b>DEMO</b>.
""",
        reply_markup=join_keyboard(),
    )


# ============================================================
# VERIFY
# ============================================================

@dp.callback_query(F.data == "verify_join")
async def verify_join(callback: CallbackQuery):
    user_id = callback.from_user.id

    if not await is_member(user_id):
        await callback.answer(
            "❌ Bạn chưa tham gia nhóm!",
            show_alert=True,
        )
        return

    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET
                verified = TRUE,
                updated_at = NOW()
            WHERE user_id = $1
        """, user_id)

    referral_added = await confirm_referral_after_verify(user_id)

    with suppress(Exception):
        await callback.message.delete()

    await callback.message.answer(
        "✅ <b>Xác minh thành công!</b>\n\n"
        "🔥 Chào mừng bạn đến SHOP ACC FREE FIRE.",
        reply_markup=shop_keyboard(),
    )

    if referral_added:
        user = await get_user(user_id)

        referrer_id = user["referrer_id"]

        if referrer_id:
            with suppress(Exception):
                await bot.send_message(
                    referrer_id,
                    "🎉 <b>Bạn vừa nhận được +2 Xu!</b>\n\n"
                    "👤 Một người bạn đã tham gia bot "
                    "thông qua link ref của bạn.",
                )

    await callback.answer("Xác minh thành công!")


# ============================================================
# REF
# ============================================================

@dp.callback_query(F.data == "my_ref")
async def my_ref(callback: CallbackQuery):
    user_id = callback.from_user.id

    me = await bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start=ref_{user_id}"
    )

    user = await get_user(user_id)

    await callback.message.edit_text(
        f"""
<b>🔗 REF CỦA BẠN</b>

👥 Đã mời:
<b>{user["ref_count"]} người</b>

💰 Xu nhận được:
<b>{user["ref_earned"]} Xu</b>

🎁 Mỗi ref hợp lệ:
<b>+2 Xu</b>

🔗 Link của bạn:

<code>{link}</code>

📌 Người được mời phải tham gia nhóm và xác minh thành công thì ref mới được tính.
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Quay lại",
                        callback_data="refresh_shop",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# ============================================================
# ACCOUNT
# ============================================================

@dp.callback_query(F.data == "account")
async def account(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)

    username = (
        f"@{user['username']}"
        if user["username"]
        else "Không có username"
    )

    await callback.message.edit_text(
        f"""
<b>👤 TÀI KHOẢN</b>

🆔 ID:
<code>{user["user_id"]}</code>

👤 Username:
<b>{username}</b>

💰 Xu:
<b>{user["balance"]}</b>

👥 Ref:
<b>{user["ref_count"]}</b>

🛒 Đã mua:
<b>{user["total_purchases"]}</b>

🔐 Trạng thái:
<b>{"Đã xác minh" if user["verified"] else "Chưa xác minh"}</b>
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Quay lại",
                        callback_data="refresh_shop",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# ============================================================
# EARN XU
# ============================================================

@dp.callback_query(F.data == "earn_xu")
async def earn_xu(callback: CallbackQuery):
    me = await bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start=ref_{callback.from_user.id}"
    )

    await callback.message.edit_text(
        f"""
<b>💰 KIẾM XU</b>

🎁 Cách kiếm Xu:

👥 Mời bạn bè tham gia bot.

⭐ Mỗi người hợp lệ:
<b>+2 Xu</b>

⚠️ Không tính:
• Tự ref chính mình
• Ref trùng
• Ref chưa xác minh nhóm

🔗 Link ref:

<code>{link}</code>
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Quay lại",
                        callback_data="refresh_shop",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# ============================================================
# REFRESH
# ============================================================

@dp.callback_query(F.data == "refresh_shop")
async def refresh_shop(callback: CallbackQuery):
    await callback.message.edit_text(
        await shop_text(callback.from_user.id),
        reply_markup=shop_keyboard(),
    )

    await callback.answer("Đã làm mới!")


# ============================================================
# PURCHASE CONFIRM
# ============================================================

@dp.callback_query(F.data.startswith("buy_"))
async def purchase_confirm(callback: CallbackQuery):
    product = callback.data.replace("buy_", "")

    if product not in PRODUCTS:
        await callback.answer(
            "❌ Sản phẩm không tồn tại.",
            show_alert=True,
        )
        return

    info = PRODUCTS[product]

    stock = await get_stock(product)
    user = await get_user(callback.from_user.id)

    if stock <= 0:
        await callback.answer(
            "❌ Sản phẩm đã hết hàng!",
            show_alert=True,
        )
        return

    if user["balance"] < info["price"]:
        await callback.answer(
            "❌ Bạn không đủ Xu!",
            show_alert=True,
        )
        return

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="✅ Xác nhận mua",
            callback_data=f"confirm_{product}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="❌ Hủy",
            callback_data="refresh_shop",
        )
    )

    await callback.message.edit_text(
        f"""
<b>🛒 XÁC NHẬN MUA</b>

📦 Sản phẩm:
<b>{info["name"]}</b>

💰 Giá:
<b>{info["price"]} Xu</b>

📦 Còn:
<b>{stock} acc</b>

💳 Số dư hiện tại:
<b>{user["balance"]} Xu</b>

💳 Sau khi mua:
<b>{user["balance"] - info["price"]} Xu</b>

⚠️ Đây là <b>TÀI KHOẢN DEMO</b>.

Bạn có chắc muốn mua?
""",
        reply_markup=builder.as_markup(),
    )

    await callback.answer()


# ============================================================
# SAFE PURCHASE TRANSACTION
# ============================================================

async def buy_account(user_id: int, product: str):
    info = PRODUCTS[product]

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            # Khóa user
            user = await conn.fetchrow("""
                SELECT user_id, balance
                FROM users
                WHERE user_id = $1
                FOR UPDATE
            """, user_id)

            if not user:
                return {
                    "ok": False,
                    "reason": "user_not_found",
                }

            if user["balance"] < info["price"]:
                return {
                    "ok": False,
                    "reason": "not_enough_xu",
                }

            # Khóa 1 acc chưa bán
            account = await conn.fetchrow("""
                SELECT id, email, password
                FROM demo_accounts
                WHERE product = $1
                  AND sold = FALSE
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """, product)

            if not account:
                return {
                    "ok": False,
                    "reason": "out_of_stock",
                }

            # Đánh dấu đã bán
            await conn.execute("""
                UPDATE demo_accounts
                SET
                    sold = TRUE,
                    sold_to = $1,
                    sold_at = NOW()
                WHERE id = $2
            """,
                user_id,
                account["id"],
            )

            # Trừ Xu
            await conn.execute("""
                UPDATE users
                SET
                    balance = balance - $2,
                    total_purchases = total_purchases + 1,
                    updated_at = NOW()
                WHERE user_id = $1
            """,
                user_id,
                info["price"],
            )

            # Lưu lịch sử mua
            await conn.execute("""
                INSERT INTO purchases (
                    user_id,
                    account_id,
                    product,
                    price,
                    email,
                    password
                )
                VALUES ($1, $2, $3, $4, $5, $6)
            """,
                user_id,
                account["id"],
                product,
                info["price"],
                account["email"],
                account["password"],
            )

            new_balance = user["balance"] - info["price"]

            return {
                "ok": True,
                "email": account["email"],
                "password": account["password"],
                "price": info["price"],
                "balance": new_balance,
                "product_name": info["name"],
            }


# ============================================================
# CONFIRM PURCHASE
# ============================================================

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_purchase(callback: CallbackQuery):
    product = callback.data.replace("confirm_", "")

    if product not in PRODUCTS:
        await callback.answer(
            "❌ Sản phẩm không tồn tại.",
            show_alert=True,
        )
        return

    result = await buy_account(
        callback.from_user.id,
        product,
    )

    if not result["ok"]:

        reason = result["reason"]

        messages = {
            "user_not_found":
                "❌ Không tìm thấy tài khoản.",
            "not_enough_xu":
                "❌ Bạn không đủ Xu.",
            "out_of_stock":
                "❌ Acc vừa hết hàng.",
        }

        await callback.answer(
            messages.get(
                reason,
                "❌ Không thể thực hiện giao dịch.",
            ),
            show_alert=True,
        )

        await callback.message.edit_text(
            await shop_text(callback.from_user.id),
            reply_markup=shop_keyboard(),
        )

        return

    await callback.message.edit_text(
        f"""
<b>🎉 MUA THÀNH CÔNG!</b>

📦 Sản phẩm:
<b>{result["product_name"]}</b>

━━━━━━━━━━━━━━━━━━

📧 Email DEMO:
<code>{result["email"]}</code>

🔑 Password DEMO:
<code>{result["password"]}</code>

━━━━━━━━━━━━━━━━━━

💸 Đã trừ:
<b>{result["price"]} Xu</b>

💰 Số dư còn:
<b>{result["balance"]} Xu</b>

⚠️ <b>ĐÂY LÀ DỮ LIỆU DEMO.</b>

Chúc bạn sử dụng vui vẻ! 🔥
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛒 Tiếp tục mua",
                        callback_data="refresh_shop",
                    )
                ]
            ]
        ),
    )

    await callback.answer("✅ Giao dịch thành công!")


# ============================================================
# ADMIN MENU
# ============================================================

def admin_keyboard():
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📦 Thêm kho",
            callback_data="admin_add_stock",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="💰 Add Xu",
            callback_data="admin_add_xu",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📊 Thống kê",
            callback_data="admin_stats",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📢 Thông báo",
            callback_data="admin_broadcast",
        )
    )

    return builder.as_markup()


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        """
<b>👑 ADMIN PANEL</b>

🔐 Chỉ admin mới nhìn thấy menu này.
""",
        reply_markup=admin_keyboard(),
    )


# ============================================================
# ADMIN ADD XU
# ============================================================

@dp.callback_query(F.data == "admin_add_xu")
async def admin_add_xu_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(
        AddXuState.enter_user_id
    )

    await callback.message.answer(
        "💰 <b>ADD XU</b>\n\n"
        "Nhập User ID cần cộng Xu:"
    )

    await callback.answer()


@dp.message(AddXuState.enter_user_id)
async def admin_add_xu_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ User ID phải là số.")
        return

    user = await get_user(user_id)

    if not user:
        await message.answer(
            "❌ User chưa từng sử dụng bot."
        )
        return

    await state.update_data(
        target_user_id=user_id
    )

    await state.set_state(
        AddXuState.enter_amount
    )

    await message.answer(
        "💰 Nhập số Xu muốn cộng:"
    )


@dp.message(AddXuState.enter_amount)
async def admin_add_xu_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        amount = int(message.text.strip())

        if amount <= 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Số Xu không hợp lệ."
        )
        return

    data = await state.get_data()
    user_id = data["target_user_id"]

    async with db_pool.acquire() as conn:
        new_balance = await conn.fetchval("""
            UPDATE users
            SET
                balance = balance + $2,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING balance
        """,
            user_id,
            amount,
        )

    await state.clear()

    await message.answer(
        f"""
✅ <b>ADD XU THÀNH CÔNG</b>

👤 User:
<code>{user_id}</code>

➕ Đã cộng:
<b>{amount} Xu</b>

💰 Số dư mới:
<b>{new_balance} Xu</b>
"""
    )

    with suppress(Exception):
        await bot.send_message(
            user_id,
            f"""
🎁 <b>Bạn vừa được cộng Xu!</b>

➕ Nhận:
<b>{amount} Xu</b>

💰 Số dư mới:
<b>{new_balance} Xu</b>
"""
        )


@dp.message(Command("addxu"))
async def addxu_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(
        AddXuState.enter_user_id
    )

    await message.answer(
        "💰 <b>ADD XU</b>\n\n"
        "Nhập User ID:"
    )


# ============================================================
# ADMIN ADD STOCK
# ============================================================

@dp.callback_query(F.data == "admin_add_stock")
async def admin_add_stock_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(
        AddStockState.choose_product
    )

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🟢 Clone Level 5–8",
            callback_data="stock_product_clone_5_8",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔵 Clone Level 30",
            callback_data="stock_product_clone_30",
        )
    )

    await callback.message.answer(
        "📦 <b>THÊM KHO DEMO</b>\n\n"
        "Chọn loại acc:",
        reply_markup=builder.as_markup(),
    )

    await callback.answer()


@dp.message(Command("themkho"))
async def themkho_command(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(
        AddStockState.choose_product
    )

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🟢 Clone Level 5–8",
            callback_data="stock_product_clone_5_8",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔵 Clone Level 30",
            callback_data="stock_product_clone_30",
        )
    )

    await message.answer(
        "📦 <b>THÊM KHO DEMO</b>\n\n"
        "Chọn loại acc:",
        reply_markup=builder.as_markup(),
    )


@dp.callback_query(F.data.startswith("stock_product_"))
async def choose_stock_product(
    callback: CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        return

    product = callback.data.replace(
        "stock_product_",
        ""
    )

    if product not in PRODUCTS:
        return

    await state.update_data(
        product=product
    )

    await state.set_state(
        AddStockState.enter_quantity
    )

    await callback.message.answer(
        f"""
📦 Loại:
<b>{PRODUCTS[product]["name"]}</b>

Nhập số lượng acc muốn thêm:

Ví dụ:
<code>10</code>
"""
    )

    await callback.answer()


@dp.message(AddStockState.enter_quantity)
async def stock_quantity(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        quantity = int(message.text.strip())

        if quantity <= 0 or quantity > 1000:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Số lượng phải từ 1 đến 1000."
        )
        return

    await state.update_data(
        quantity=quantity
    )

    await state.set_state(
        AddStockState.enter_accounts
    )

    await message.answer(
        f"""
📦 Cần nhập:
<b>{quantity} acc DEMO</b>

Mỗi dòng một tài khoản theo định dạng:

<code>email|password</code>

Ví dụ:

<code>
demo001@gmail.com|password123
demo002@gmail.com|password456
demo003@gmail.com|password789
</code>

⚠️ Nhập đúng <b>{quantity} dòng</b>.
"""
    )


@dp.message(AddStockState.enter_accounts)
async def stock_accounts(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()

    product = data["product"]
    quantity = data["quantity"]

    lines = [
        line.strip()
        for line in message.text.splitlines()
        if line.strip()
    ]

    if len(lines) != quantity:
        await message.answer(
            f"❌ Cần đúng {quantity} dòng.\n"
            f"Hiện tại nhận được {len(lines)} dòng."
        )
        return

    accounts = []

    for index, line in enumerate(lines, 1):

        if "|" not in line:
            await message.answer(
                f"❌ Dòng {index} sai định dạng.\n\n"
                "Phải là:\n"
                "<code>email|password</code>"
            )
            return

        email, password = line.split(
            "|",
            1
        )

        email = email.strip()
        password = password.strip()

        if not email or not password:
            await message.answer(
                f"❌ Dòng {index} không hợp lệ."
            )
            return

        accounts.append(
            (email, password)
        )

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            await conn.executemany(
                """
                INSERT INTO demo_accounts (
                    product,
                    email,
                    password
                )
                VALUES ($1, $2, $3)
                """,
                [
                    (
                        product,
                        email,
                        password
                    )
                    for email, password in accounts
                ]
            )

    await state.clear()

    stock = await get_stock(product)

    await message.answer(
        f"""
✅ <b>THÊM KHO THÀNH CÔNG</b>

📦 Loại:
<b>{PRODUCTS[product]["name"]}</b>

➕ Đã thêm:
<b>{quantity} acc DEMO</b>

📊 Kho hiện tại:
<b>{stock} acc</b>
"""
    )


# ============================================================
# ADMIN STATS
# ============================================================

async def get_stats():
    async with db_pool.acquire() as conn:

        users = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE verified = TRUE) AS verified,
                COALESCE(SUM(ref_count), 0) AS refs,
                COALESCE(SUM(ref_earned), 0) AS ref_xu,
                COALESCE(SUM(balance), 0) AS xu
            FROM users
        """)

        purchases = await conn.fetchval("""
            SELECT COUNT(*)
            FROM purchases
        """)

        result = {
            "users": users,
            "purchases": purchases,
        }

        for product in PRODUCTS:

            stock = await conn.fetchval("""
                SELECT COUNT(*)
                FROM demo_accounts
                WHERE product = $1
                  AND sold = FALSE
            """, product)

            sold = await conn.fetchval("""
                SELECT COUNT(*)
                FROM demo_accounts
                WHERE product = $1
                  AND sold = TRUE
            """, product)

            result[product] = {
                "stock": stock,
                "sold": sold,
            }

        return result


async def stats_text():
    s = await get_stats()
    u = s["users"]

    return f"""
<b>📊 THỐNG KÊ SHOP</b>

👤 Tổng user:
<b>{u["total"]}</b>

✅ Đã verify:
<b>{u["verified"]}</b>

👥 Tổng ref:
<b>{u["refs"]}</b>

🎁 Tổng Xu từ ref:
<b>{u["ref_xu"]}</b>

💰 Tổng Xu đang giữ:
<b>{u["xu"]}</b>

🛒 Tổng lượt mua:
<b>{s["purchases"]}</b>

━━━━━━━━━━━━━━━━━━

🟢 <b>Clone Level 5–8</b>

📦 Còn:
<b>{s["clone_5_8"]["stock"]}</b>

📤 Đã bán:
<b>{s["clone_5_8"]["sold"]}</b>

━━━━━━━━━━━━━━━━━━

🔵 <b>Clone Level 30</b>

📦 Còn:
<b>{s["clone_30"]["stock"]}</b>

📤 Đã bán:
<b>{s["clone_30"]["sold"]}</b>
"""


@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.answer(
        await stats_text()
    )

    await callback.answer()


@dp.message(Command("thongke"))
async def admin_stats_command(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        await stats_text()
    )


# ============================================================
# ADMIN BROADCAST
# ============================================================

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(
        BroadcastState.waiting_content
    )

    await callback.message.answer(
        """
📢 <b>THÔNG BÁO</b>

Nhập nội dung muốn gửi cho tất cả user.

Bot sẽ gửi lần lượt để hạn chế spam/rate-limit.
"""
    )

    await callback.answer()


@dp.message(Command("thongbao"))
async def broadcast_command(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) == 1:
        await state.set_state(
            BroadcastState.waiting_content
        )

        await message.answer(
            "📢 Nhập nội dung thông báo:"
        )

        return

    await execute_broadcast(
        message,
        parts[1]
    )


@dp.message(BroadcastState.waiting_content)
async def broadcast_content(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    content = message.text.strip()

    await state.clear()

    await execute_broadcast(
        message,
        content
    )


async def execute_broadcast(
    admin_message: Message,
    content: str
):
    if not content:
        await admin_message.answer(
            "❌ Nội dung trống."
        )
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetch("""
            SELECT user_id
            FROM users
            WHERE started = TRUE
        """)

    success = 0
    failed = 0

    status_msg = await admin_message.answer(
        "📢 Đang gửi thông báo...\n"
        "⏳ 0 / 0"
    )

    total = len(users)

    for index, row in enumerate(users, 1):

        try:
            await bot.send_message(
                row["user_id"],
                f"📢 <b>THÔNG BÁO</b>\n\n{content}"
            )

            success += 1

        except (
            TelegramForbiddenError,
            TelegramBadRequest
        ):
            failed += 1

        except Exception as e:
            logger.warning(
                "Broadcast error %s: %s",
                row["user_id"],
                e,
            )
            failed += 1

        # Chống rate limit
        await asyncio.sleep(0.05)

        if index % 25 == 0 or index == total:
            with suppress(Exception):
                await status_msg.edit_text(
                    f"📢 <b>Đang gửi thông báo...</b>\n\n"
                    f"📨 {index} / {total}\n"
                    f"✅ Thành công: {success}\n"
                    f"❌ Thất bại: {failed}"
                )

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO broadcasts (
                admin_id,
                content,
                success,
                failed
            )
            VALUES ($1, $2, $3, $4)
        """,
            admin_message.from_user.id,
            content,
            success,
            failed,
        )

    await status_msg.edit_text(
        f"""
✅ <b>THÔNG BÁO HOÀN TẤT</b>

👥 Tổng user:
<b>{total}</b>

✅ Thành công:
<b>{success}</b>

❌ Thất bại:
<b>{failed}</b>
"""
    )


# ============================================================
# ADMIN CALLBACK MENU
# ============================================================

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.edit_text(
        "<b>👑 ADMIN PANEL</b>",
        reply_markup=admin_keyboard(),
    )

    await callback.answer()


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

async def health(request):
    return web.Response(
        text="OK - Free Fire Demo Shop Bot"
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    port = int(
        os.getenv("PORT", "10000")
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    logger.info(
        "Health server running on port %s",
        port,
    )

    return runner


# ============================================================
# MAIN
# ============================================================

async def main():

    global db_pool

    await init_db()

    # Web server cho Render / UptimeRobot
    web_runner = await start_web_server()

    try:
        logger.info(
            "🔥 FREE FIRE DEMO SHOP BOT STARTED"
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:

        with suppress(Exception):
            await bot.session.close()

        if web_runner:
            await web_runner.cleanup()

        if db_pool:
            await db_pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
