import json
import re

from thefuzz import fuzz

from .config import DATA_DIR, MEMORY_FILE, MODEL
from .clients import xai
from .api import with_retry


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {}


def save_memory(memory: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))


def get_user_notes(user_id: int, memory: dict) -> str:
    return memory.get(str(user_id), {}).get("notes", "")


def extract_mentioned_user_ids(text: str) -> list[str]:
    """Extract Discord user IDs from mention format <@123456> or <@!123456>."""
    return re.findall(r'<@!?(\d+)>', text)


def find_referenced_users(text: str, memory: dict, exclude_user_id: int = None, mentioned_ids: list[str] = None) -> dict[str, str]:
    """Find users mentioned by name in text using fuzzy matching, plus explicit Discord mentions."""
    referenced = {}
    text_lower = text.lower()
    words = re.findall(r'\b\w+\b', text_lower)

    # First, add any explicitly mentioned users by Discord ID
    if mentioned_ids:
        for uid in mentioned_ids:
            if exclude_user_id and str(exclude_user_id) == uid:
                continue
            if uid in memory:
                data = memory[uid]
                username = data.get("username", "")
                notes = data.get("notes", "")
                if username and notes:
                    referenced[username] = notes

    # Then do fuzzy matching for names mentioned in text
    for user_id, data in memory.items():
        if exclude_user_id and str(exclude_user_id) == user_id:
            continue
        username = data.get("username", "")
        notes = data.get("notes", "")
        if not username or not notes:
            continue
        if username in referenced:  # Already added via explicit mention
            continue

        if username.lower() in text_lower:
            referenced[username] = notes
            continue

        for word in words:
            if len(word) >= 3 and fuzz.ratio(username.lower(), word) >= 80:
                referenced[username] = notes
                break

    return referenced


async def update_user_notes(user_id: int, username: str, message: str, memory: dict):
    current = memory.get(str(user_id), {}).get("notes", "No prior notes.")

    response = await with_retry(
        xai.chat.completions.create,
        model=MODEL,
        messages=[{
            "role": "user",
            "content": f"""Update your notes about {username} based on this message.

Current notes: {current}
Their message: {message}

Write 2-3 sentences about their interests, personality, and what they care about. If nothing new, return current notes unchanged."""
        }],
    )

    memory[str(user_id)] = {"username": username, "notes": response.choices[0].message.content}
    save_memory(memory)
