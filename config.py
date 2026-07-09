"""Configuration and environment loading for the Content Unlock Bot.

Local-only setup. The bot token and admin username are read from a .env file
(or the environment) and never change at runtime.

The download/website/shortener URLs and the list of required channels are
mutable settings managed at runtime via admin commands and persisted in
settings.json (see settings.py). The .env values for those URLs are only used
as the initial seed the first time the bot starts.
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


# --------------------------------------------------------------------------- #
# Fixed configuration (never changes at runtime)
# --------------------------------------------------------------------------- #

# Telegram bot token from @BotFather.
BOT_TOKEN = _require("BOT_TOKEN")

# Admin Telegram username (with or without leading @).
ADMIN_USERNAME = _require("ADMIN_USERNAME").lstrip("@")


# --------------------------------------------------------------------------- #
# Initial seed values (used only on first run, then managed via admin commands)
# --------------------------------------------------------------------------- #

# Shortener URL opened by the "Unlock Download" button.
SHORTENER_URL = _require("SHORTENER_URL")

# Website opened by the "Open Website" button.
WEBSITE_URL = _require("WEBSITE_URL")

# Direct file download (MediaFire or similar) opened by "Download File".
MEDIAFIRE_URL = _require("MEDIAFIRE_URL")

# Default required channels seeded into settings.json on first run.
#
#   id    -> chat identifier passed to the Telegram API. For public channels
#            this is the @username. For private channels use the numeric chat
#            id (e.g. -1001234567890) - the bot must be an admin of that
#            channel for membership checks to work.
#   title -> human-readable label shown on the "Join" buttons.
#   url   -> invite link the "Join" button opens.
DEFAULT_CHANNELS = [
    {
        "id": "@hydraholo",
        "title": "Hydra Holo",
        "url": "https://t.me/hydraholo",
    },
    {
        "id": "@sgcheats04",
        "title": "SG Cheats 04",
        "url": "https://t.me/sgcheats04",
    },
    {
        "id": "@SGCheats",
        "title": "SG Cheats",
        "url": "https://t.me/SGCheats",
    },
]

# Local JSON data stores (no cloud storage).
USERS_FILE = "users.json"
STATS_FILE = "stats.json"
SETTINGS_FILE = "settings.json"

WELCOME_TEXT = (
    "\U0001F44B *Welcome to the Content Unlock Bot!*\n\n"
    "To unlock the download you must first join all of our channels.\n\n"
    "1\uFE0F\u20E3 Tap *Join Channels* and join every channel.\n"
    "2\uFE0F\u20E3 Come back and tap *Verify Channels*.\n\n"
    "Once verified, you'll get access to the download."
)
