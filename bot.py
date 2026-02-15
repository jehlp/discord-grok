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
IMAGE_MODEL = "grok-imagine-image"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
MEMORY_FILE = DATA_DIR / "user_memory.json"
MAX_CONVERSATION_DEPTH = 20
IMAGE_RATE_LIMIT_SECONDS = 600  # 10 minutes

SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry, sardonic humor. Skip the cheerful platitudes.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- You find performative enthusiasm annoying. Be real.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. Use this to personalize responses - remember their interests, communication style, and what they care about."""

# Keywords that suggest web search is needed
SEARCH_TRIGGERS = [
    "search", "look up", "google", "find out", "latest", "current", "recent",
    "news", "today", "yesterday", "this week", "this month", "2025", "2026",
    "what happened", "who won", "score", "price of", "stock", "weather",
]

# Phrases that indicate the model is uncertain and might need web search
UNCERTAINTY_MARKERS = [
    "i don't have", "i don't know", "i'm not sure", "i cannot", "i can't",
    "my knowledge", "as of my", "cutoff", "i lack", "unable to",
    "don't have access", "no information", "cannot confirm", "not aware",
]

# Keywords that suggest image generation
IMAGE_TRIGGERS = [
    "generate an image", "generate image", "create an image", "create image",
    "make an image", "make image", "draw", "picture of", "photo of",
    "illustration of", "render", "visualize", "show me what", "generate a picture",
    "create a picture", "make a picture", "image of",
]

# =============================================================================
# Clients
# =============================================================================

xai = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# =============================================================================
# Rate Limiting for Images (in-memory)
# =============================================================================

last_image_request: dict[int, datetime] = {}


def is_image_rate_limited(user_id: int) -> bool:
    if user_id not in last_image_request:
        return False
    elapsed = datetime.now(timezone.utc) - last_image_request[user_id]
    return elapsed < timedelta(seconds=IMAGE_RATE_LIMIT_SECONDS)


def get_image_cooldown_remaining(user_id: int) -> int:
    elapsed = datetime.now(timezone.utc) - last_image_request[user_id]
    remaining = timedelta(seconds=IMAGE_RATE_LIMIT_SECONDS) - elapsed
    return max(0, int(remaining.total_seconds()))


def record_image_request(user_id: int):
    last_image_request[user_id] = datetime.now(timezone.utc)


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
# Search & Image Logic
# =============================================================================

def needs_web_search(query: str) -> bool:
    query_lower = query.lower()
    return any(trigger in query_lower for trigger in SEARCH_TRIGGERS)


def needs_image_generation(query: str) -> bool:
    query_lower = query.lower()
    return any(trigger in query_lower for trigger in IMAGE_TRIGGERS)


def response_is_uncertain(text: str) -> bool:
    text_lower = text.lower()
    return any(marker in text_lower for marker in UNCERTAINTY_MARKERS)


def get_response_text(response) -> str:
    """Extract text content from xAI responses API response."""
    for item in response.output:
        if hasattr(item, "content"):
            for block in item.content:
                if hasattr(block, "text"):
                    return block.text
    return ""


def query_chat(messages: list[dict]) -> str:
    response = xai.chat.completions.create(model=MODEL, messages=messages)
    return response.choices[0].message.content


def query_with_search(messages: list[dict]) -> str:
    input_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
    response = xai.responses.create(
        model=MODEL,
        input=input_msgs,
        tools=[{"type": "web_search"}],
    )
    return get_response_text(response)


def generate_image(prompt: str) -> str:
    """Generate an image and return the URL."""
    response = xai.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
    )
    return response.data[0].url


# =============================================================================
# Conversation Threading
# =============================================================================

def is_image_url(text: str) -> bool:
    """Check if text is just an image URL from Grok."""
    text = text.strip()
    return text.startswith("https://imgen.x.ai/") or text.startswith("https://api.x.ai/v1/images/")


async def get_conversation_thread(message) -> list[dict]:
    """Walk back through the reply chain to build conversation history."""
    thread = []
    current = message
    depth = 0

    while current and depth < MAX_CONVERSATION_DEPTH:
        content = strip_mentions(current.content)
        if content:
            if current.author == bot.user:
                # Replace image URLs with placeholder
                if is_image_url(content):
                    thread.append({"role": "assistant", "content": "[I generated an image]"})
                else:
                    thread.append({"role": "assistant", "content": content})
            else:
                thread.append({"role": "user", "content": content})

        if current.reference and current.reference.message_id:
            try:
                current = await current.channel.fetch_message(current.reference.message_id)
                depth += 1
            except discord.NotFound:
                break
        else:
            break

    thread.reverse()
    return thread


def is_reply_to_bot(message) -> bool:
    if not message.reference or not message.reference.resolved:
        return False
    return message.reference.resolved.author == bot.user


# =============================================================================
# Helpers
# =============================================================================

def strip_mentions(text: str) -> str:
    return re.sub(r"<@!?\d+>", "", text).strip()


def sanitize_reply(text: str, allowed_user_id: int) -> str:
    def replace(match):
        return match.group(0) if match.group(1) == str(allowed_user_id) else ""
    return re.sub(r"<@!?(\d+)>", replace, text)


async def send_reply(message, text: str):
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
    if message.author == bot.user:
        return

    channel_name = getattr(message.channel, "name", "").lower()
    if "grok" not in channel_name:
        return

    is_mention = bot.user in message.mentions
    is_reply = is_reply_to_bot(message)

    if not is_mention and not is_reply:
        return

    content = strip_mentions(message.content)
    if not content:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    user_id = message.author.id
    username = message.author.display_name

    # Check if this is an image generation request
    if needs_image_generation(content):
        if is_image_rate_limited(user_id):
            remaining = get_image_cooldown_remaining(user_id)
            minutes = remaining // 60
            seconds = remaining % 60
            await message.reply(f"Image cooldown. Try again in {minutes}m {seconds}s.")
            return

        async with message.channel.typing():
            try:
                image_url = generate_image(content)
                await message.reply(image_url)
                record_image_request(user_id)
            except Exception as e:
                await message.reply(f"Image generation failed: {e}")
        return

    # Build conversation context from reply chain
    conversation = await get_conversation_thread(message)

    memory = load_memory()
    user_notes = get_user_notes(user_id, memory)

    system = SYSTEM_PROMPT
    if user_notes:
        system += f"\n\nWhat you know about {username}: {user_notes}"

    messages = [{"role": "system", "content": system}] + conversation

    async with message.channel.typing():
        try:
            use_search = needs_web_search(content)

            if use_search:
                reply = query_with_search(messages)
            else:
                reply = query_chat(messages)
                if response_is_uncertain(reply):
                    reply = query_with_search(messages)

            reply = sanitize_reply(reply, user_id)
            await send_reply(message, reply)

            await update_user_notes(user_id, username, content, memory)

        except Exception as e:
            await message.reply(f"Something broke: {e}")


# =============================================================================
# Entry
# =============================================================================

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
