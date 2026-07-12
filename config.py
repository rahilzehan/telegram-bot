"""Configuration and environment loading for the Content Unlock Bot.

Local-only setup. The bot token and admin IDs are read from a .env file
(or the environment) and never change at runtime.

The list of required channels is managed at runtime via admin commands
(/addchannel, /removechannel) and persisted in settings.json (see settings.py).
"""

import os

try:
    # Load variables from a local .env file for local testing.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional.
    pass


def _require(name: str) -> str:
    """Return a required environment variable or raise a helpful error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to your .env file before starting the bot."
        )
    return value


def _parse_admin_ids() -> list[int]:
    """Parse ADMIN_IDS from environment variable.
    
    ADMIN_IDS can be a comma-separated list of numeric Telegram user IDs.
    Example: ADMIN_IDS=123456789,987654321
    """
    value = os.environ.get("ADMIN_IDS", "").strip()
    if not value:
        raise RuntimeError(
            "ADMIN_IDS is not set. Add it to your .env file before starting the bot. "
            "Format: ADMIN_IDS=123456789,987654321"
        )
    try:
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    except ValueError:
        raise RuntimeError(
            "ADMIN_IDS must be a comma-separated list of numeric Telegram user IDs. "
            "Example: ADMIN_IDS=123456789,987654321"
        )


# --------------------------------------------------------------------------- #
# Fixed configuration (never changes at runtime)
# --------------------------------------------------------------------------- #

# Telegram bot token from @BotFather.
BOT_TOKEN = _require("BOT_TOKEN")

# Admin Telegram user IDs (comma-separated list of numeric IDs).
ADMIN_IDS = _parse_admin_ids()


# --------------------------------------------------------------------------- #
# Local JSON data stores (no cloud storage).
# --------------------------------------------------------------------------- #

USERS_FILE = "users.json"
STATS_FILE = "stats.json"
SETTINGS_FILE = "settings.json"

WELCOME_TEXT = (
    "╔════════════════════╗\n"
    "      🚀 ZXERA BOT\n"
    "╚════════════════════╝\n\n"
    "👋 *Welcome to ZXERA BOT!*\n\n"
    "To unlock downloads, you must join all required channels.\n\n"
    "1️⃣ Tap *Join Channels* and join every channel.\n"
    "2️⃣ Come back and tap *Verify Channels*.\n\n"
    "Once verified, you'll get instant access to all downloads."
)