"""Channel membership verification helpers.

These helpers wrap Telegram's get_chat_member API so the bot can confirm a
user has joined every required channel. The channel list is read live from
the runtime settings store, so admin changes take effect immediately.
Supports both public (@username) and private (numeric chat id) channels.
"""

import logging

from telegram.error import TelegramError

from settings import get_channels

logger = logging.getLogger(__name__)

# Telegram member statuses that count as "joined".
_JOINED_STATUSES = {"member", "administrator", "creator"}


def _normalize_chat_id(channel_id):
    """Return the chat id in the form the Telegram API expects.

    Private channels are identified by a numeric chat id (e.g. -1001234567890).
    Stored as a string, these must be sent to the API as an ``int`` or Telegram
    replies "Chat not found". Public @usernames are passed through unchanged.
    """
    if isinstance(channel_id, int):
        return channel_id
    text = str(channel_id).strip()
    # Numeric chat id (private channels), possibly negative.
    if text.lstrip("-").isdigit():
        return int(text)
    return text


async def is_member_of_channel(bot, channel_id, user_id: int) -> bool:
    """Return True if user_id is a member of the given channel.

    Any API error (bot not admin, user never started, channel invalid, etc.)
    is treated as "not a member" so access fails closed.
    """
    chat_id = _normalize_chat_id(channel_id)
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramError as exc:
        logger.warning("Membership check failed for %s in %s: %s", user_id, chat_id, exc)
        return False
    return member.status in _JOINED_STATUSES


async def get_missing_channels(bot, user_id: int) -> list:
    """Return the list of required channel configs the user has NOT joined."""
    missing = []
    for channel in get_channels():
        if not await is_member_of_channel(bot, channel["id"], user_id):
            missing.append(channel)
    return missing


async def is_verified(bot, user_id: int) -> bool:
    """Return True only if the user has joined every required channel."""
    return not await get_missing_channels(bot, user_id)
