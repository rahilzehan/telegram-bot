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
admins.json : list of admin telegram_ids (managed at runtime)
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone

from config import ADMIN_IDS, STATS_FILE, USERS_FILE

logger = logging.getLogger(__name__)

_lock = threading.Lock()

ADMINS_FILE = "admins.json"


# Expose _load_users for other modules, if needed
def _get_users_data() -> list:
    return _load_users()


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


def _normalize_user_record(user: dict) -> dict:
    """Ensure a user record has all required fields with safe defaults.
    
    This function migrates old user records to the new schema by adding
    any missing fields with appropriate defaults. No data is deleted.
    """
    # Ensure all required fields exist
    normalized = {
        "telegram_id": user.get("telegram_id"),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "verified": user.get("verified", False),
        "unlock_requests": user.get("unlock_requests", 0),
        "first_seen": user.get("first_seen", _now()),
        "last_seen": user.get("last_seen", _now()),
        "referred_by": user.get("referred_by"),  # None if not present
        "referral_count": user.get("referral_count", 0),
    }
    return normalized

def _load_users() -> list:
    data = _load(USERS_FILE, [])
    if not isinstance(data, list):
        return []
    
    # Normalize all user records and check if migration is needed
    normalized_users = [_normalize_user_record(u) for u in data]
    
    # Check if normalization changed anything (migration needed)
    if len(data) != len(normalized_users) or any(
        _normalize_user_record(u) != u for u in data
    ):
        # Migration needed - save the normalized data
        _atomic_write(USERS_FILE, normalized_users)
        logger.info("Migrated %d user records to new schema", len(normalized_users))
    
    return normalized_users


def _load_stats() -> dict:
    data = _load(STATS_FILE, {})
    return data if isinstance(data, dict) else {}


def _load_admins() -> list:
    """Load admin IDs from admins.json. If file doesn't exist, seed from config.ADMIN_IDS."""
    data = _load(ADMINS_FILE, [])
    if not data:  # First run - seed from config
        data = ADMIN_IDS
        _atomic_write(ADMINS_FILE, data)
    return data if isinstance(data, list) else []


def _save_admins(admins: list) -> None:
    """Save admin IDs to admins.json."""
    _atomic_write(ADMINS_FILE, admins)


def upsert_user(telegram_id: int, username, first_name, referred_by: int = None) -> dict:
    """Create or update a user record. Returns the stored record.
    
    Args:
        telegram_id: Telegram user ID
        username: Telegram username
        first_name: Telegram first name
        referred_by: If this is a new user, the user_id who referred them (one-time only)
    """
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
            "referred_by": referred_by,
            "referral_count": 0,
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


# --------------------------------------------------------------------------- #
# Admin management
# --------------------------------------------------------------------------- #
def get_admins() -> list:
    """Return the current list of admin telegram IDs."""
    return _load_admins()


def is_admin(telegram_id: int) -> bool:
    """Check if a user is an admin."""
    return telegram_id in _load_admins()


def add_admin(telegram_id: int) -> bool:
    """Add an admin. Returns False if already an admin."""
    with _lock:
        admins = _load_admins()
        if telegram_id in admins:
            return False
        admins.append(telegram_id)
        _save_admins(admins)
        return True


def remove_admin(telegram_id: int) -> bool:
    """Remove an admin. Returns False if not an admin."""
    with _lock:
        admins = _load_admins()
        if telegram_id not in admins:
            return False
        admins.remove(telegram_id)
        _save_admins(admins)
        return True


# --------------------------------------------------------------------------- #
# Referral system
# --------------------------------------------------------------------------- #
def get_referral_count(telegram_id: int) -> int:
    """Return the number of users who were referred by this user and verified."""
    users = _load_users()
    count = sum(1 for u in users if u.get("referred_by") == telegram_id and u.get("verified"))
    return count


def get_referrer(telegram_id: int) -> int:
    """Return the user_id who referred this user, or None."""
    users = _load_users()
    for user in users:
        if user.get("telegram_id") == telegram_id:
            return user.get("referred_by")
    return None


def set_referrer(telegram_id: int, referred_by: int) -> bool:
    """Set the referrer for a user. Returns False if already set or invalid.
    
    Args:
        telegram_id: The user being referred
        referred_by: The referrer's user_id
    
    Returns:
        True if referrer was set, False if already set or self-referral
    """
    if telegram_id == referred_by:
        return False  # No self-referrals
    
    with _lock:
        users = _load_users()
        for user in users:
            if user.get("telegram_id") == telegram_id:
                if user.get("referred_by") is not None:
                    return False  # Already has a referrer
                user["referred_by"] = referred_by
                _atomic_write(USERS_FILE, users)
                return True
        return False


def get_top_referrers(limit: int = 10) -> list:
    """Return top referrers as list of (user_id, count) tuples.
    
    Only includes users who have made successful referrals.
    """
    users = _load_users()
    referrer_counts = {}
    
    for user in users:
        if user.get("referred_by") and user.get("verified"):
            referrer_id = user["referred_by"]
            referrer_counts[referrer_id] = referrer_counts.get(referrer_id, 0) + 1
    
    # Sort by count descending, then by user_id for consistency
    sorted_referrers = sorted(
        referrer_counts.items(),
        key=lambda x: (-x[1], x[0])
    )
    return sorted_referrers[:limit]


def reset_referral_for_user(telegram_id: int) -> bool:
    """Reset referral data for a user (admin action). Returns False if user not found.
    
    This resets the referrer assignment for a user, allowing them to use a new referral.
    """
    with _lock:
        users = _load_users()
        for user in users:
            if user.get("telegram_id") == telegram_id:
                user["referred_by"] = None
                _atomic_write(USERS_FILE, users)
                return True
        return False


def get_all_referral_data() -> dict:
    """Get all referral data for admin viewing."""
    users = _load_users()
    data = {}
    for user in users:
        user_id = user.get("telegram_id")
        if user_id:
            data[user_id] = {
                "referred_by": user.get("referred_by"),
                "count": sum(1 for u in users if u.get("referred_by") == user_id and u.get("verified")),
                "verified": user.get("verified"),
            }
    return data
