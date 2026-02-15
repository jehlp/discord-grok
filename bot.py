import os
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from openai import OpenAI

# =============================================================================
# Configuration
# =============================================================================

MODEL = "grok-4-1-fast-reasoning"
DATA_DIR = Path("/app/data")
MEMORY_FILE = DATA_DIR / "user_memory.json"
RATE_LIMIT_SECONDS = 300  # 5 minutes

SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry, sardonic humor. Skip the cheerful platitudes.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- You find performative enthusiasm annoying. Be real.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. Use this to personalize responses - remember their interests, communication style, and what they care about."""

# =============================================================================
# Clients
# =============================================================================

xai = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# =============================================================================
# Rate Limiting (in-memory)
# =============================================================================

last_request: dict[int, datetime] = {}


def is_rate_limited(user_id: int) -> bool:
    if user_id not in last_request:
        return False
    elapsed = datetime.now(timezone.utc) - last_request[user_id]
    return elapsed < timedelta(seconds=RATE_LIMIT_SECONDS)


def get_cooldown_remaining(user_id: int) -> int:
    elapsed = datetime.now(timezone.utc) - last_request[user_id]
    remaining = timedelta(seconds=RATE_LIMIT_SECONDS) - elapsed
    return max(0, int(remaining.total_seconds()))


def record_request(user_id: int):
    last_request[user_id] = datetime.now(timezone.utc)


# =============================================================================
# User Memory (persistent)
# =============================================================================

def load_memory() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {}


def save_memory(memory: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))


def get_user_notes(user_id: int, memory: dict) -> str:
    return memory.get(str(user_id), {}).get("notes", "")


async def update_user_notes(user_id: int, username: str, message: str, memory: dict):
    current = memory.get(str(user_id), {}).get("notes", "No prior notes.")

    response = xai.chat.completions.create(
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


# =============================================================================
# Helpers
# =============================================================================

def strip_mentions(text: str) -> str:
    return re.sub(r"<@!?\d+>", "", text).strip()


def sanitize_reply(text: str, allowed_user_id: int) -> str:
    """Remove all @mentions except the allowed user."""
    def replace(match):
        return match.group(0) if match.group(1) == str(allowed_user_id) else ""
    return re.sub(r"<@!?(\d+)>", replace, text)


async def get_user_context_messages(channel, author, before_msg) -> list[str]:
    """Get the user's recent messages (last 5 min) for context."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    messages = []

    async for msg in channel.history(limit=20, before=before_msg):
        if msg.author == author and msg.created_at > cutoff:
            messages.append(strip_mentions(msg.content))
            if len(messages) >= 5:
                break

    return list(reversed(messages))


async def send_reply(message, text: str):
    """Send reply, splitting if over Discord's limit."""
    if len(text) <= 2000:
        await message.reply(text)
        return

    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
    for i, chunk in enumerate(chunks):
        if i == 0:
            await message.reply(chunk)
        else:
            await message.channel.send(chunk)


# =============================================================================
# Bot Events
# =============================================================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    # Ignore self
    if message.author == bot.user:
        return

    # Only respond to mentions
    if bot.user not in message.mentions:
        return

    content = strip_mentions(message.content)
    if not content:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    user_id = message.author.id
    username = message.author.display_name
    channel_name = getattr(message.channel, "name", "").lower()

    # Rate limit (skip if channel has "grok" in name)
    if "grok" not in channel_name and is_rate_limited(user_id):
        remaining = get_cooldown_remaining(user_id)
        minutes = remaining // 60
        seconds = remaining % 60
        await message.reply(f"Slow down. Try again in {minutes}m {seconds}s.")
        return

    # Build context
    memory = load_memory()
    user_notes = get_user_notes(user_id, memory)
    recent_messages = await get_user_context_messages(message.channel, message.author, message)

    system = SYSTEM_PROMPT
    if user_notes:
        system += f"\n\nWhat you know about {username}: {user_notes}"
    if recent_messages:
        system += f"\n\n{username}'s recent messages:\n" + "\n".join(f"- {m}" for m in recent_messages)

    # Query Grok
    async with message.channel.typing():
        try:
            response = xai.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
            reply = sanitize_reply(response.choices[0].message.content, user_id)
            await send_reply(message, reply)

            record_request(user_id)
            await update_user_notes(user_id, username, content, memory)

        except Exception as e:
            await message.reply(f"Something broke: {e}")


# =============================================================================
# Entry
# =============================================================================

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
