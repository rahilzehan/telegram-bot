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
from datetime import datetime
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

from config import ADMIN_IDS, BOT_TOKEN, WELCOME_TEXT
from settings import (
    add_channel,
    add_file,
    edit_file,
    get_channels,
    get_files,
    remove_channel,
    remove_file,
    set_file_thumbnail,
)
from storage import (
    add_admin,
    all_user_ids,
    count_total_users,
    count_unlock_requests,
    count_verified_users,
    get_admins,
    get_referral_count,
    get_referrer,
    get_top_referrers,
    is_admin,
    record_unlock_request,
    remove_admin,
    reset_referral_for_user,
    set_referrer,
    set_verified,
    upsert_user,
    _get_users_data,
    _lock,
    _load_users,
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

# Conversation state for thumbnail upload
AWAITING_THUMBNAIL = "awaiting_thumbnail"
THUMBNAIL_FILE_ID = "thumbnail_file_id"


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


def download_keyboard(exclude_thumbnail_files: bool = False) -> InlineKeyboardMarkup:
    """Final download buttons shown after successful verification.
    
    Args:
        exclude_thumbnail_files: If True, exclude files that have thumbnails
                                 (they'll be sent separately with the photo)
    """
    files = get_files()
    if exclude_thumbnail_files:
        files = [f for f in files if not f.get('thumbnail')]
    
    if not files:
        if exclude_thumbnail_files:
            return None  # No files without thumbnails
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("No files available", callback_data="no_files")]
            ]
        )

    keyboard = [
        [InlineKeyboardButton(f"{file['name']}", url=file["url"])] for file in files
    ]
    return InlineKeyboardMarkup(keyboard)


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
    return bool(user and is_admin(user.id))


async def _deny(update: Update) -> None:
    await update.message.reply_text("\u26D4 You are not authorized to use this command.")


async def get_user_data(user_id: int) -> dict:
    """Retrieve user data from storage.py based on telegram_id.
    This function is required to dynamically display user-specific data in the dashboard.
    """
    with _lock:
        users = _load_users()
        for user in users:
            if user.get("telegram_id") == user_id:
                return user
        return {}


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start: greet the user and show join/verify buttons.
    
    Supports referral system: /start USER_ID will track the referrer.
    """
    user = update.effective_user
    referred_by = None
    
    # Check for referral parameter
    if context.args and context.args[0].isdigit():
        potential_referrer = int(context.args[0])
        if potential_referrer != user.id:  # No self-referrals
            referred_by = potential_referrer
    
    upsert_user(user.id, user.username, user.first_name, referred_by=referred_by)
    logger.info("User %s (%s) started the bot", user.id, user.username)
    
    # Create keyboard with join/verify buttons plus support links
    keyboard = [list(row) for row in join_channels_keyboard().inline_keyboard]
    keyboard.append([
        InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/Will_byers07"),
        InlineKeyboardButton("📢 Official Channel", url="https://t.me/zxeralikebotbywilliam07"),
    ])
    keyboard.append([
        InlineKeyboardButton("💬 Support Group", url="https://t.me/likebotbyzxera"),
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the premium formatted help panel for normal users."""
    user = update.effective_user
    if not user:
        return

    user_data = await get_user_data(user.id)
    if not user_data:
        upsert_user(user.id, user.username, user.first_name)
        user_data = await get_user_data(user.id)

    # Get dynamic data
    total_files = len(get_files())
    required_channels_count = len(get_channels())
    bot_version = "1.0.0"  # Placeholder, update as needed
    server_status = "Online"  # Placeholder, actual check might be more complex
    shortener_status = "Disabled"
    download_system_status = "Operational" if total_files > 0 else "Offline"

    # User Account Details
    telegram_id = user.id
    telegram_username = user.username.replace("_", "\\_") if user.username else "N/A"
    display_name = user.first_name if user.first_name else "N/A"
    verification_status = "Verified ✅" if user_data.get("verified") else "Not Verified ❌"
    join_date = datetime.fromisoformat(user_data["first_seen"]).strftime("%Y-%m-%d %H:%M:%S") if user_data.get("first_seen") else "N/A"
    last_activity = datetime.fromisoformat(user_data["last_seen"]).strftime("%Y-%m-%d %H:%M:%S") if user_data.get("last_seen") else "N/A"

    # FAQ Answers
    faq_verification_failed = "If verification failed, ensure you have joined all required channels and try again. Sometimes, Telegram delays update. Please wait a few minutes before trying again."
    faq_download_missing = "If a download is missing, it might be due to temporary issues or the file being removed. Please contact support.\nDeveloper: @Will\\_byers07"
    faq_shortener_problem = "If you are having issues with the shortener, try clearing your browser cache or using a different browser. Make sure to complete all steps in the shortener.\nDeveloper: @Will\\_byers07"

    # Construct the premium dashboard message
    message_text = f"""
✨ *Premium Help Dashboard* ✨

🤖 *ZXERA BOT*
Version: {bot_version}
Current Server Status: {server_status}

━━━━━━━━━━━━━━━━━━

👤 *Account*
• Telegram ID: `{telegram_id}`
• Telegram Username: @{telegram_username}
• Display Name: {display_name}
• Verification Status: {verification_status}
• Join Date: {join_date}
• Last Activity: {last_activity}

━━━━━━━━━━━━━━━━━━

📂 *Download Center*
• Total Available Files: {total_files} (Live Count)
• Required Channels: {required_channels_count} (Live Count)
• Download System Status: {download_system_status}

━━━━━━━━━━━━━━━━━━

🚀 *Features*
✅ Secure Verification
✅ Fast Unlock
✅ Unlimited Downloads
✅ Safe Download Links
✅ Automatic Updates

━━━━━━━━━━━━━━━━━━

📖 *How To Use*
1. Join Required Channels
2. Verify
3. Open Shortener
4. Complete Shortener
5. Download Files

━━━━━━━━━━━━━━━━━━

❓ *FAQ*

*Verification Failed?*
{faq_verification_failed}

*Download Missing?*
{faq_download_missing}

*Shortener Problem?*
{faq_shortener_problem}

━━━━━━━━━━━━━━━━━━

📞 *Support*
    Developer: @Will\_byers07
Official Channel: https://t.me/zxeralikebotbywilliam07
Support Group: https://t.me/likebotbyzxera

━━━━━━━━━━━━━━━━━━
"""

    # Inline buttons
    keyboard = [
        [InlineKeyboardButton("📂 Files", callback_data="user_files")],
        [InlineKeyboardButton("📢 Channels", callback_data="user_channels")],
        [InlineKeyboardButton("📞 Support", url="https://t.me/likebotbyzxera"),
         InlineKeyboardButton("📖 Guide", callback_data="how_to_use")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
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
    
    # Send files with thumbnails first
    files_with_thumbnails = [f for f in get_files() if f.get('thumbnail')]
    for file in files_with_thumbnails:
        caption = f"*{file['name']}*"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⬇️ Download {file['name']}", url=file["url"])]
        ])
        await query.message.reply_photo(
            photo=file['thumbnail'],
            caption=caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    # Send remaining files without thumbnails as buttons
    keyboard_no_thumb = download_keyboard(exclude_thumbnail_files=True)
    if keyboard_no_thumb:
        await query.message.reply_text(
            " *Your Downloads*",
            reply_markup=keyboard_no_thumb,
            parse_mode=ParseMode.MARKDOWN,
        )
    elif not files_with_thumbnails:
        # No files at all
        await query.message.reply_text(
            " *Verified!*\n\nNo files available.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        # Only thumbnails, add intro message
        await query.message.reply_text(
            " *Verified!*\n\nHere are your downloads:",
            parse_mode=ParseMode.MARKDOWN,
        )


# The 'completed' handler is no longer needed since we show downloads directly after verification.
# Kept for backward compatibility but does nothing.
async def completed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the 'I Completed' button (legacy)."""
    query = update.callback_query
    await query.answer()
    # Send files with thumbnails first
    files_with_thumbnails = [f for f in get_files() if f.get('thumbnail')]
    for file in files_with_thumbnails:
        caption = f"*{file['name']}*"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⬇️ Download {file['name']}", url=file["url"])]
        ])
        await query.message.reply_photo(
            photo=file['thumbnail'],
            caption=caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
    
    # Send remaining files without thumbnails as buttons
    keyboard_no_thumb = download_keyboard(exclude_thumbnail_files=True)
    if keyboard_no_thumb:
        await query.message.reply_text(
            "\U0001F389 *Here is your download!*",
            reply_markup=keyboard_no_thumb,
            parse_mode=ParseMode.MARKDOWN,
        )
    elif not files_with_thumbnails:
        # No files at all
        await query.message.reply_text(
            "\U0001F389 *Here is your download!*\n\nNo files available.",
            parse_mode=ParseMode.MARKDOWN,
        )


# --------------------------------------------------------------------------- #
# Admin handlers - settings (take effect immediately, persisted to settings.json)
# --------------------------------------------------------------------------- #
# URL setting commands removed - files are managed via /addfile, /editfile, /removefile


async def addfile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addfile <url> <button name> [thumbnail_file_id] - Add a downloadable file.
    
    Optional thumbnail_file_id: Telegram file_id of an image to use as thumbnail.
    To get a file_id, send an image to the bot and use the file_id from the message.
    """
    if not _is_admin(update):
        return await _deny(update)
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addfile <url> <button name> [thumbnail_file_id]\n"
            "Example: /addfile https://example.com/file.zip \"My File\" AgACAgIAAxkBAA..."
        )
        return
    file_url = context.args[0]
    button_name = " ".join(context.args[1:])
    thumbnail = context.args[2] if len(context.args) > 2 else None
    
    # Generate a unique ID. Simple increment for now, could be UUID in future.
    files = get_files()
    file_ids = [f["id"] for f in files]
    new_id = 1
    while new_id in file_ids:
        new_id += 1

    if add_file(new_id, button_name, file_url, thumbnail):
        msg = f"\u2705 File added successfully.\nID: {new_id}\nName: {button_name}"
        if thumbnail:
            msg += f"\nThumbnail: Set"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("\u26A0\uFE0F Failed to add file (duplicate ID or other error).")


async def editfile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/editfile <id> <new_url> <new_button_name> [new_thumbnail_file_id] - Edit an existing file.
    
    Optional new_thumbnail_file_id: New Telegram file_id of an image to use as thumbnail.
    Use empty string "" to remove existing thumbnail.
    """
    if not _is_admin(update):
        return await _deny(update)
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /editfile <id> <new_url> <new_button_name> [new_thumbnail_file_id]\n"
            "Example: /editfile 1 https://example.com/new.zip \"New Name\" AgACAgIAAxkBAA...\n"
            "To remove thumbnail: /editfile 1 https://example.com/new.zip \"New Name\" \"\""
        )
        return
    try:
        file_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid file ID. Must be a number.")
        return
    new_url = context.args[1]
    new_button_name = " ".join(context.args[2:])
    new_thumbnail = context.args[3] if len(context.args) > 3 else None
    
    if edit_file(file_id, new_url, new_button_name, new_thumbnail):
        msg = "\u2705 File updated successfully."
        if new_thumbnail is not None:
            if new_thumbnail:
                msg += " Thumbnail updated."
            else:
                msg += " Thumbnail removed."
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text(f"\u26A0\uFE0F No file found with ID {file_id}.")


async def removefile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removefile <id> - Remove a downloadable file."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /removefile <id>")
        return
    try:
        file_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid file ID. Must be a number.")
        return
    if remove_file(file_id):
        await update.message.reply_text("\u2705 File removed successfully.")
    else:
        await update.message.reply_text(f"\u26A0\uFE0F No file found with ID {file_id}.")


async def listfiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/listfiles - Display all downloadable files."""
    if not _is_admin(update):
        return await _deny(update)
    files = get_files()
    if not files:
        await update.message.reply_text("No downloadable files configured.")
        return
    lines = []
    for file in files:
        # HTML escape the file name to handle special characters safely
        escaped_name = (file['name']
                       .replace('&', '&')
                       .replace('<', '<')
                       .replace('>', '>'))
        line = f"{file['id']}. {escaped_name}"
        if file.get('thumbnail'):
            line += " 🖼️"
        lines.append(line)
    await update.message.reply_text(
        "📋 <b>Downloadable Files</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def setthumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setthumbnail <file_id> - Set thumbnail for a file.
    
    After running this command, send an image to the bot to set as thumbnail.
    Use /setthumbnail <file_id> remove to remove the thumbnail.
    """
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text(
            "Usage: /setthumbnail <file_id> [remove]\n"
            "After running, send an image to set as thumbnail.\n"
            "Use 'remove' to delete existing thumbnail."
        )
        return
    
    try:
        file_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid file ID. Must be a number.")
        return
    
    files = get_files()
    file = next((f for f in files if f["id"] == file_id), None)
    if not file:
        await update.message.reply_text(f"\u26A0\uFE0F No file found with ID {file_id}.")
        return
    
    if len(context.args) > 1 and context.args[1].lower() == "remove":
        if set_file_thumbnail(file_id, ""):
            await update.message.reply_text(f"\u2705 Thumbnail removed for file {file_id}.")
        else:
            await update.message.reply_text(f"\u26A0\uFE0F Failed to remove thumbnail.")
        return
    
    # Store the file_id in user_data and wait for photo
    context.user_data[AWAITING_THUMBNAIL] = True
    context.user_data[THUMBNAIL_FILE_ID] = file_id
    await update.message.reply_text(
        f"📸 Ready to set thumbnail for file {file_id} ({file['name']}).\n"
        "Please send an image now."
    )


async def handle_thumbnail_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo upload when admin is setting a thumbnail."""
    if not _is_admin(update):
        return
    if not context.user_data.get(AWAITING_THUMBNAIL):
        return
    
    if not update.message.photo:
        await update.message.reply_text("Please send an image (photo), not a file.")
        return
    
    # Get the largest photo size
    photo = update.message.photo[-1]
    file_id = context.user_data.pop(THUMBNAIL_FILE_ID)
    context.user_data.pop(AWAITING_THUMBNAIL, None)
    
    if set_file_thumbnail(file_id, photo.file_id):
        await update.message.reply_text(
            f"\u2705 Thumbnail set successfully for file {file_id}!\n"
            f"File ID: {photo.file_id}"
        )
    else:
        await update.message.reply_text(f"\u26A0\uFE0F Failed to set thumbnail.")


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
    lines = []
    for i, ch in enumerate(channels, start=1):
        # HTML escape the channel title and URL to handle special characters safely
        escaped_title = (ch['title']
                        .replace('&', '&')
                        .replace('<', '<')
                        .replace('>', '>'))
        escaped_url = (ch['url']
                      .replace('&', '&')
                      .replace('<', '<')
                      .replace('>', '>'))
        line = f"{i}. <b>{escaped_title}</b> - <code>{ch['id']}</code>\n   {escaped_url}"
        lines.append(line)
    await update.message.reply_text(
        "📋 <b>Required Channels</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
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
# Admin handlers - admin management
# --------------------------------------------------------------------------- #
async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addadmin <user_id> - add a new admin."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    if add_admin(user_id):
        await update.message.reply_text(f"\u2705 Admin added: {user_id}")
    else:
        await update.message.reply_text(f"\u26A0\uFE0F User {user_id} is already an admin.")


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removeadmin <user_id> - remove an admin."""
    if not _is_admin(update):
        return await _deny(update)
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Must be a number.")
        return
    if remove_admin(user_id):
        await update.message.reply_text(f"\u2705 Admin removed: {user_id}")
    else:
        await update.message.reply_text(f"\u26A0\uFE0F User {user_id} is not an admin.")


async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admins - list all admins."""
    if not _is_admin(update):
        return await _deny(update)
    admin_list = get_admins()
    if not admin_list:
        await update.message.reply_text("No admins configured.")
        return
    lines = [f" `{admin_id}`" for admin_id in admin_list]
    await update.message.reply_text(
        "\U0001F4CB *Admins*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )

# --------------------------------------------------------------------------- #
# Referral system commands
# --------------------------------------------------------------------------- #
async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/referral - Show your referral count and personal referral link."""
    user = update.effective_user
    if not user:
        return
    
    ref_count = get_referral_count(user.id)
    ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start={user.id}"
    
    message = (
        "🔗 *Your Referral Program*\n\n"
        f"*Referrals:* {ref_count}\n"
        f"*Your Link:* `{ref_link}`\n\n"
        "Share your link to earn referrals!\n"
        "You get credit when they join and verify."
    )
    
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def topref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/topref - Show the top referrers leaderboard."""
    top_referrers = get_top_referrers(limit=10)
    
    if not top_referrers:
        await update.message.reply_text("No referrals yet!")
        return
    
    lines = [
        f"{i}. User `{user_id}`: {count} referral{'s' if count != 1 else ''}"
        for i, (user_id, count) in enumerate(top_referrers, start=1)
    ]
    
    message = "🏆 *Top Referrers*\n\n" + "\n".join(lines)
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def adminref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/adminref - Admin command to view referral statistics."""
    if not _is_admin(update):
        return await _deny(update)
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /adminref <view|reset_user> [user_id]\n"
            "Examples:\n"
            "/adminref view\n"
            "/adminref reset_user 123456789"
        )
        return
    
    action = context.args[0].lower()
    
    if action == "view":
        top_referrers = get_top_referrers(limit=20)
        if not top_referrers:
            await update.message.reply_text("No referral data available.")
            return
        
        lines = [
            f"{i}. User `{user_id}`: {count} verified referral{'s' if count != 1 else ''}"
            for i, (user_id, count) in enumerate(top_referrers, start=1)
        ]
        
        message = "📊 *Referral Statistics (Top 20)*\n\n" + "\n".join(lines)
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    
    elif action == "reset_user":
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /adminref reset_user <user_id>")
            return
        try:
            target_user_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("Invalid user ID.")
            return
        
        if reset_referral_for_user(target_user_id):
            await update.message.reply_text(f" Referral data reset for user {target_user_id}.")
        else:
            await update.message.reply_text(f" User {target_user_id} not found.")
    
    else:
        await update.message.reply_text("Unknown action. Use 'view' or 'reset_user'.")


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
    application.add_handler(CommandHandler("help", help_command))

    # Admin settings commands.
    application.add_handler(CommandHandler("addfile", addfile))
    application.add_handler(CommandHandler("editfile", editfile))
    application.add_handler(CommandHandler("removefile", removefile))
    application.add_handler(CommandHandler("listfiles", listfiles))
    application.add_handler(CommandHandler("setthumbnail", setthumbnail))
    application.add_handler(CommandHandler("addchannel", addchannel))
    application.add_handler(CommandHandler("removechannel", removechannel))
    application.add_handler(CommandHandler("listchannels", listchannels))
    application.add_handler(CommandHandler("stats", stats))
    # Admin management commands.
    application.add_handler(CommandHandler("addadmin", addadmin))
    application.add_handler(CommandHandler("removeadmin", removeadmin))
    application.add_handler(CommandHandler("admins", admins))
    application.add_handler(CommandHandler("broadcast", broadcast_start))
    application.add_handler(CommandHandler("cancel", broadcast_cancel))
    
    # Referral commands.
    application.add_handler(CommandHandler("referral", referral))
    application.add_handler(CommandHandler("topref", topref))
    application.add_handler(CommandHandler("adminref", adminref))

    # Callback queries.
    application.add_handler(CallbackQueryHandler(verify, pattern=f"^{CB_VERIFY}$"))
    application.add_handler(CallbackQueryHandler(completed, pattern=f"^{CB_COMPLETED}$"))
    application.add_handler(CallbackQueryHandler(lambda update, context: None, pattern=f"^no_files$")) # Ignore "No files available" button

    # Photo handler for thumbnail upload (must come before text handler)
    application.add_handler(MessageHandler(filters.PHOTO, handle_thumbnail_upload))

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