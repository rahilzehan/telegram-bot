"""Local JSON storage for users and statistics.

Kept deliberately simple so the bot runs with zero external infrastructure.
A lock guards concurrent writes from the async event loop.

Data model
----------
users.json : list of user records:
    {
        "telegram_id": int,
        "username": str | None,
        "first_name": str | None,
        "verified": bool,            # last known verification result
        "unlock_requests": int,      # number of unlock requests made
        "first_seen": iso8601 str,
        "last_seen": iso8601 str,
    }
stats.json : {"unlock_requests": int}
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone

from config import STATS_FILE, USERS_FILE

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not read %s, starting fresh", path)
        return default


def _atomic_write(path: str, data) -> None:
    """Write data to disk atomically to avoid corruption."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _load_users() -> list:
    data = _load(USERS_FILE, [])
    return data if isinstance(data, list) else []


def _load_stats() -> dict:
    data = _load(STATS_FILE, {})
    return data if isinstance(data, dict) else {}


def upsert_user(telegram_id: int, username, first_name) -> dict:
    """Create or update a user record. Returns the stored record."""
    with _lock:
        users = _load_users()
        for user in users:
            if user.get("telegram_id") == telegram_id:
                user["username"] = username
                user["first_name"] = first_name
                user["last_seen"] = _now()
                _atomic_write(USERS_FILE, users)
                return user
        record = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "verified": False,
            "unlock_requests": 0,
            "first_seen": _now(),
            "last_seen": _now(),
        }
        users.append(record)
        _atomic_write(USERS_FILE, users)
        return record


def set_verified(telegram_id: int, verified: bool) -> None:
    """Persist the latest verification result for a user."""
    with _lock:
        users = _load_users()
        for user in users:
            if user.get("telegram_id") == telegram_id:
                user["verified"] = verified
                user["last_seen"] = _now()
                _atomic_write(USERS_FILE, users)
                return


def record_unlock_request(telegram_id: int) -> None:
    """Increment the unlock request counters for a user and globally."""
    with _lock:
        users = _load_users()
        for user in users:
            if user.get("telegram_id") == telegram_id:
                user["unlock_requests"] = user.get("unlock_requests", 0) + 1
                user["last_seen"] = _now()
                break
        _atomic_write(USERS_FILE, users)

        stats = _load_stats()
        stats["unlock_requests"] = stats.get("unlock_requests", 0) + 1
        _atomic_write(STATS_FILE, stats)


def count_verified_users() -> int:
    """Return the number of users currently marked as verified."""
    return sum(1 for u in _load_users() if u.get("verified"))


def count_total_users() -> int:
    """Return the total number of known users."""
    return len(_load_users())


def count_unlock_requests() -> int:
    """Return the total number of unlock requests ever made."""
    return int(_load_stats().get("unlock_requests", 0))


def all_user_ids() -> list:
    """Return every known telegram_id (used for broadcasting)."""
    return [u.get("telegram_id") for u in _load_users() if u.get("telegram_id")]
