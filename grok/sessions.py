import asyncio
from datetime import datetime, timedelta, timezone

from .config import MAX_CONVERSATION_DEPTH, SESSION_TTL_SECONDS

# { user_id: { "messages": [{"role":..., "content":...}], "last_active": datetime } }
active_sessions: dict[int, dict] = {}


def get_session(user_id: int) -> dict | None:
    """Get a user's active session if it exists and hasn't expired."""
    session = active_sessions.get(user_id)
    if not session:
        return None
    elapsed = datetime.now(timezone.utc) - session["last_active"]
    if elapsed > timedelta(seconds=SESSION_TTL_SECONDS):
        del active_sessions[user_id]
        return None
    return session


def update_session(user_id: int, messages: list[dict]):
    """Replace a user's session with the given messages, capped to MAX_CONVERSATION_DEPTH."""
    capped = messages[-(MAX_CONVERSATION_DEPTH * 2):]  # keep last N exchanges
    active_sessions[user_id] = {
        "messages": capped,
        "last_active": datetime.now(timezone.utc),
    }


async def cleanup_sessions():
    """Periodically prune expired sessions."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        now = datetime.now(timezone.utc)
        expired = [
            uid for uid, s in active_sessions.items()
            if (now - s["last_active"]) > timedelta(seconds=SESSION_TTL_SECONDS)
        ]
        for uid in expired:
            del active_sessions[uid]
