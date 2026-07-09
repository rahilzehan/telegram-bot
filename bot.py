#!/usr/bin/env python3
"""Content Unlock Bot - Telegram bot gating downloads behind channel joins.

Users must join every required channel before they can unlock a download.
Membership is re-checked before every unlock request, so leaving a channel
immediately revokes access. Built on python-telegram-bot (v20+, async).

Download/website/shortener URLs and the required-channel list are managed at
runtime via admin commands and persisted in settings.json. Changes take effect
immediately, without editing code or restarting the bot.
"""

import asyncio
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import ADMIN_USERNAME, BOT_TOKEN, WELCOME_TEXT
from settings import (
    add_channel,
    get_channels,
    get_mediafire_url,
    get_shortener_url,
    get_website_url,
    remove_channel,
    set_mediafire_url,
    set_shortener_url,
    set_website_url,
)
from storage import (
    all_user_ids,
    count_total_users,
    count_unlock_requests,
    count_verified_users,
    record_unlock_request,
    set_verified,
    upsert_user,
)
from validators import get_missing_channels

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("content_unlock_bot")

# --------------------------------------------------------------------------- #
# Callback data identifiers
# --------------------------------------------------------------------------- #
CB_VERIFY = "verify"
CB_COMPLETED = "completed"

# Conversation state key for an in-progress broadcast.
AWAITING_BROADCAST = "awaiting_broadcast"


# --------------------------------------------------------------------------- #
# Keyboards
# --------------------------------------------------------------------------- #
def join_channels_keyboard() -> InlineKeyboardMarkup:
    """Join buttons for every required channel plus a Verify button."""
    keyboard = [
        [InlineKeyboardButton(f"\U0001F4E2 Join {ch['title']}", url=ch["url"])]
        for ch in get_channels()
    ]
    keyboard.append(
        [InlineKeyboardButton("\u2705 Verify Channels", callback_data=CB_VERIFY)]
    )
    return InlineKeyboardMarkup(keyboard)


def unlock_keyboard() -> InlineKeyboardMarkup:
    """Shown after successful verification: open the shortener."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001F513 Youtube", url=get_shortener_url())],
            [InlineKeyboardButton("\u2705 I Completed", callback_data=CB_COMPLETED)],
        ]
    )


def download_keyboard() -> InlineKeyboardMarkup:
    """Final download buttons shown after the user completes the shortener."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001F4E6 Download File", url=get_mediafire_url())],
            [InlineKeyboardButton("\U0001F310 Open Website", url=get_website_url())],
        ]
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _send_join_prompt(message, missing) -> None:
    """Tell the user which channels are still required."""
    names = "\n".join(f"\u2022 {ch['title']}" for ch in missing)
    text = (
        "\U0001F512 *Access locked*\n\n"
        "You still need to join the following channel(s):\n"
        f"{names}\n\n"
        "Join them, then tap *Verify Channels* again."
    )
    await message.reply_text(
        text,
        reply_markup=join_channels_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.username and user.username.lower() == ADMIN_USERNAME.lower())


async def _deny(update: Update) -> None:
    await update.message.reply_text("\u26D4 You are not authorized to use this command.")


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start: greet the user and show join/verify buttons."""
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name)
    logger.info("User %s (%s) started the bot", user.id, user.username)
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=join_channels_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-check membership and unlock access or re-prompt to join."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    missing = await get_missing_channels(context.bot, user.id)
    if missing:
        set_verified(user.id, False)
        await _send_join_prompt(query.message, missing)
        return

    set_verified(user.id, True)
    record_unlock_request(user.id)
    logger.info("User %s verified successfully", user.id)
    await query.message.reply_text(
        "\u2705 *Verified!*\n\nTap the button below to unlock your download.",
        reply_markup=unlock_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def completed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the 'I Completed' button: re-verify, then show download links."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    # Security: re-check membership before granting the final download.
    missing = await get_missing_channels(context.bot, user.id)
    if missing:
        set_verified(user.id, False)
        await _send_join_prompt(query.message, missing)
        return

    set_verified(user.id, True)
    await query.message.reply_text(
        "\U0001F389 *Here is your download!*",
        reply_markup=download_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


# --------------------------------------------------------------------------- #
# Admin handlers - settings (take effect immediately, persisted to settings.json)
# --------------------------------------------------------------------------- #
async def setfile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setfile <url> - set the MediaFire/direct download URL."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /setfile <url>")
        return
    set_mediafire_url(context.args[0])
    await update.message.reply_text(f"\u2705 Download file URL updated to:\n{context.args[0]}")


async def setwebsite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setwebsite <url> - set the website URL."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /setwebsite <url>")
        return
    set_website_url(context.args[0])
    await update.message.reply_text(f"\u2705 Website URL updated to:\n{context.args[0]}")


async def setshortener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setshortener <url> - set the shortener URL."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /setshortener <url>")
        return
    set_shortener_url(context.args[0])
    await update.message.reply_text(f"\u2705 Shortener URL updated to:\n{context.args[0]}")


async def addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addchannel <id> <url> [title] - add a required channel.

    <id>  : @username (public) or numeric chat id (private, e.g. -1001234567890)
    <url> : the join/invite link
    title : optional display name (defaults to the id)
    """
    if not _is_admin(update):
        return await _deny(update)
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addchannel <id> <url> [title]\n"
            "Example: /addchannel @mychannel https://t.me/mychannel My Channel"
        )
        return
    channel_id = context.args[0]
    url = context.args[1]
    title = " ".join(context.args[2:]) if len(context.args) > 2 else channel_id
    if add_channel(channel_id, title, url):
        await update.message.reply_text(f"\u2705 Channel added: {title} ({channel_id})")
    else:
        await update.message.reply_text(f"\u26A0\uFE0F Channel {channel_id} already exists.")


async def removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removechannel <id> - remove a required channel."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /removechannel <id>")
        return
    channel_id = context.args[0]
    if remove_channel(channel_id):
        await update.message.reply_text(f"\u2705 Channel removed: {channel_id}")
    else:
        await update.message.reply_text(f"\u26A0\uFE0F No channel found with id {channel_id}.")


async def listchannels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/listchannels - show all required channels."""
    if not _is_admin(update):
        return await _deny(update)
    channels = get_channels()
    if not channels:
        await update.message.reply_text("No required channels are configured.")
        return
    lines = [
        f"{i}. *{ch['title']}* - `{ch['id']}`\n   {ch['url']}"
        for i, ch in enumerate(channels, start=1)
    ]
    await update.message.reply_text(
        "\U0001F4CB *Required channels*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats - show user and unlock statistics."""
    if not _is_admin(update):
        return await _deny(update)
    text = (
        "\U0001F4CA *Bot Statistics*\n\n"
        f"*Total users:* {count_total_users()}\n"
        f"*Verified users:* {count_verified_users()}\n"
        f"*Unlock requests:* {count_unlock_requests()}\n"
        f"*Required channels:* {len(get_channels())}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# --------------------------------------------------------------------------- #
# Admin handlers - broadcast
# --------------------------------------------------------------------------- #
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast - prompt the admin for the message text."""
    if not _is_admin(update):
        return await _deny(update)
    context.user_data[AWAITING_BROADCAST] = True
    await update.message.reply_text(
        "\U0001F4E3 Send the message you want to broadcast to all users.\n"
        "Send /cancel to abort."
    )


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a pending broadcast."""
    if context.user_data.pop(AWAITING_BROADCAST, None):
        await update.message.reply_text("Broadcast cancelled.")


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free text from the admin: deliver a pending broadcast."""
    if not (_is_admin(update) and context.user_data.get(AWAITING_BROADCAST)):
        return
    context.user_data.pop(AWAITING_BROADCAST, None)
    message = update.message.text
    sent = failed = 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            sent += 1
        except Exception:  # noqa: BLE001 - user may have blocked the bot.
            failed += 1
        await asyncio.sleep(0.05)  # be gentle with rate limits.
    await update.message.reply_text(
        f"\u2705 Broadcast finished.\nDelivered: {sent}\nFailed: {failed}"
    )


# --------------------------------------------------------------------------- #
# Error handler
# --------------------------------------------------------------------------- #
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any uncaught error raised while handling an update."""
    logger.error("Exception while handling an update:", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Application bootstrap
# --------------------------------------------------------------------------- #
def build_application() -> Application:
    """Create and configure the Telegram Application."""
    application = Application.builder().token(BOT_TOKEN).build()

    # User commands.
    application.add_handler(CommandHandler("start", start))

    # Admin settings commands.
    application.add_handler(CommandHandler("setfile", setfile))
    application.add_handler(CommandHandler("setwebsite", setwebsite))
    application.add_handler(CommandHandler("setshortener", setshortener))
    application.add_handler(CommandHandler("addchannel", addchannel))
    application.add_handler(CommandHandler("removechannel", removechannel))
    application.add_handler(CommandHandler("listchannels", listchannels))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast_start))
    application.add_handler(CommandHandler("cancel", broadcast_cancel))

    # Callback queries.
    application.add_handler(CallbackQueryHandler(verify, pattern=f"^{CB_VERIFY}$"))
    application.add_handler(CallbackQueryHandler(completed, pattern=f"^{CB_COMPLETED}$"))

    # Admin free-text handler (used for broadcasting). Must come last.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text)
    )

    application.add_error_handler(error_handler)
    return application


def main() -> None:
    """Run the bot in long-polling mode."""
    logger.info("Starting Content Unlock Bot...")

    # Ensure a current event loop exists for the main thread (Python 3.14+).
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("event loop is closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
