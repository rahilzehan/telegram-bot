"""Runtime, editable settings for the Content Unlock Bot.

The list of required channels and downloadable files live here
rather than in code, so admin commands can change them live without editing
source or restarting the bot. Everything is persisted to settings.json and
reloaded on every read, guaranteeing changes take effect immediately.

On first run the store is seeded with empty channels and files lists.
Channels are managed only via /addchannel and /removechannel commands.
"""

import json
import logging
import os
import tempfile
import threading

from config import SETTINGS_FILE

logger = logging.getLogger(__name__)

_lock = threading.RLock()


def _defaults() -> dict:
    """Return default values for settings - empty channels and files."""
    return {
        "channels": [],
        "files": [],
    }


def _read_raw() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not read %s, using defaults", SETTINGS_FILE)
        return {}


def _atomic_write(data: dict) -> None:
    directory = os.path.dirname(os.path.abspath(SETTINGS_FILE))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, SETTINGS_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _load() -> dict:
    """Return the full settings dict, backfilling any missing keys from defaults."""
    with _lock:
        data = _read_raw()
        defaults = _defaults()
        changed = False
        for key, value in defaults.items():
            if key not in data:
                data[key] = value
                changed = True
        if changed:
            _atomic_write(data)
        return data


def _save_value(key: str, value) -> None:
    with _lock:
        data = _load()
        data[key] = value
        _atomic_write(data)


# --------------------------------------------------------------------------- #
# File management
# --------------------------------------------------------------------------- #
def get_files() -> list:
    """Return the current list of downloadable files."""
    return [dict(f) for f in _load().get("files", [])]


def add_file(file_id: int, name: str, url: str, thumbnail: str = None) -> bool:
    """Add a file. Returns False if a file with that id already exists.
    
    Args:
        file_id: Unique identifier for the file
        name: Button display name
        url: Download URL
        thumbnail: Optional Telegram file_id of a thumbnail image
    """
    with _lock:
        data = _load()
        files = data.get("files", [])
        if any(f["id"] == file_id for f in files):
            return False
        file_data = {"id": file_id, "name": name, "url": url}
        if thumbnail:
            file_data["thumbnail"] = thumbnail
        files.append(file_data)
        data["files"] = files
        _atomic_write(data)
        return True


def edit_file(file_id: int, new_url: str, new_name: str, new_thumbnail: str = None) -> bool:
    """Edit an existing file. Returns False if no matching file was found.
    
    Args:
        file_id: File to edit
        new_url: New download URL
        new_name: New button display name
        new_thumbnail: Optional new thumbnail file_id (set to empty string to remove)
    """
    with _lock:
        data = _load()
        files = data.get("files", [])
        for f_item in files:
            if f_item["id"] == file_id:
                f_item["url"] = new_url
                f_item["name"] = new_name
                if new_thumbnail is not None:
                    if new_thumbnail:
                        f_item["thumbnail"] = new_thumbnail
                    elif "thumbnail" in f_item:
                        del f_item["thumbnail"]
                data["files"] = files
                _atomic_write(data)
                return True
        return False


def remove_file(file_id: int) -> bool:
    """Remove a file by id. Returns False if no matching file was found."""
    with _lock:
        data = _load()
        files = data.get("files", [])
        remaining = [f_item for f_item in files if f_item["id"] != file_id]
        if len(remaining) == len(files):
            return False
        data["files"] = remaining
        _atomic_write(data)
        return True


def set_file_thumbnail(file_id: int, thumbnail: str) -> bool:
    """Set or update a file's thumbnail. Returns False if file not found.
    
    Args:
        file_id: File to update
        thumbnail: Telegram file_id of the thumbnail (empty string to remove)
    """
    with _lock:
        data = _load()
        files = data.get("files", [])
        for f_item in files:
            if f_item["id"] == file_id:
                if thumbnail:
                    f_item["thumbnail"] = thumbnail
                elif "thumbnail" in f_item:
                    del f_item["thumbnail"]
                data["files"] = files
                _atomic_write(data)
                return True
        return False


# --------------------------------------------------------------------------- #
# Channel management
# --------------------------------------------------------------------------- #
def _normalize_id(channel_id) -> str:
    """Normalize a channel identifier for storage and comparison.

    Private channel chat ids (e.g. -1001234567890) are stored as their string
    form so the JSON store stays uniform; public @usernames are kept verbatim.
    Surrounding whitespace is stripped. Returns a string in all cases.
    """
    return str(channel_id).strip()


def _same_channel(ch: dict, target: str) -> bool:
    """Return True if the stored channel matches ``target``.

    A channel matches when ``target`` equals (case-insensitively) its stored
    id, its @username with or without the leading '@', or its invite/join url.
    This lets /removechannel accept a chat id, a username, or an invite link.
    """
    target_l = target.lower()
    stored_id = str(ch.get("id", "")).lower()
    if target_l == stored_id:
        return True
    # Allow matching a username with/without the leading '@'.
    if target_l.lstrip("@") == stored_id.lstrip("@") and stored_id.startswith("@"):
        return True
    # Allow matching by the stored invite/join url.
    if target_l == str(ch.get("url", "")).lower():
        return True
    return False


def get_channels() -> list:
    """Return the current list of required channels."""
    return [dict(ch) for ch in _load().get("channels", [])]


def add_channel(channel_id: str, title: str, url: str) -> bool:
    """Add a channel. Returns False if a channel with that id already exists."""
    channel_id = _normalize_id(channel_id)
    with _lock:
        data = _load()
        channels = data.get("channels", [])
        if any(str(ch["id"]).lower() == channel_id.lower() for ch in channels):
            return False
        channels.append({"id": channel_id, "title": title, "url": url})
        data["channels"] = channels
        _atomic_write(data)
        return True


def remove_channel(channel_id: str) -> bool:
    """Remove a channel by id, @username, or invite link.

    Returns False if no matching channel was found.
    """
    target = _normalize_id(channel_id)
    with _lock:
        data = _load()
        channels = data.get("channels", [])
        remaining = [ch for ch in channels if not _same_channel(ch, target)]
        if len(remaining) == len(channels):
            return False
        data["channels"] = remaining
        _atomic_write(data)
        return True