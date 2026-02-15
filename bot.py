import os
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
import chromadb
from openai import OpenAI
from thefuzz import fuzz

# =============================================================================
# Configuration
# =============================================================================

MODEL = "grok-4-1-fast-reasoning"
IMAGE_MODEL = "grok-imagine-image"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
MEMORY_FILE = DATA_DIR / "user_memory.json"
CHROMA_DIR = DATA_DIR / "chroma"
MAX_CONVERSATION_DEPTH = 20
IMAGE_RATE_LIMIT_SECONDS = 600
RAG_RESULTS = 10  # Number of relevant messages to retrieve

SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry, sardonic humor. Skip the cheerful platitudes.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- You find performative enthusiasm annoying. Be real.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. You also have knowledge of past conversations in this server."""

SEARCH_TRIGGERS = [
    "search", "look up", "google", "find out", "latest", "current", "recent",
    "news", "today", "yesterday", "this week", "this month", "2025", "2026",
    "what happened", "who won", "score", "price of", "stock", "weather",
]

UNCERTAINTY_MARKERS = [
    "i don't have", "i don't know", "i'm not sure", "i cannot", "i can't",
    "my knowledge", "as of my", "cutoff", "i lack", "unable to",
    "don't have access", "no information", "cannot confirm", "not aware",
]

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
intents.messages = True
intents.guilds = True
bot = discord.Client(intents=intents)

# ChromaDB setup
CHROMA_DIR.mkdir(parents=True, exist_ok=True)
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
message_collection = chroma_client.get_or_create_collection(
    name="server_messages",
    metadata={"hnsw:space": "cosine"}
)

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
# User Memory (persistent JSON)
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


def find_referenced_users(text: str, memory: dict, exclude_user_id: int = None) -> dict[str, str]:
    """Find users mentioned by name in text using fuzzy matching."""
    referenced = {}
    text_lower = text.lower()
    words = re.findall(r'\b\w+\b', text_lower)

    for user_id, data in memory.items():
        if exclude_user_id and str(exclude_user_id) == user_id:
            continue
        username = data.get("username", "")
        notes = data.get("notes", "")
        if not username or not notes:
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
# Message Store (ChromaDB)
# =============================================================================

def store_message(message_id: str, content: str, author: str, channel: str, timestamp: str):
    """Store a message in ChromaDB for RAG retrieval."""
    if not content or len(content.strip()) < 3:
        return

    try:
        message_collection.upsert(
            ids=[message_id],
            documents=[content],
            metadatas=[{
                "author": author,
                "channel": channel,
                "timestamp": timestamp,
            }]
        )
    except Exception as e:
        print(f"Failed to store message: {e}")


def retrieve_relevant_context(query: str, exclude_ids: list[str] = None) -> list[dict]:
    """Retrieve relevant past messages for context."""
    try:
        results = message_collection.query(
            query_texts=[query],
            n_results=RAG_RESULTS,
        )

        context = []
        if results and results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                msg_id = results["ids"][0][i] if results["ids"] else None
                if exclude_ids and msg_id in exclude_ids:
                    continue
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                context.append({
                    "content": doc,
                    "author": metadata.get("author", "Unknown"),
                    "channel": metadata.get("channel", "Unknown"),
                })
        return context
    except Exception as e:
        print(f"RAG retrieval failed: {e}")
        return []


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
    response = xai.images.generate(model=IMAGE_MODEL, prompt=prompt)
    return response.data[0].url


# =============================================================================
# Conversation Threading
# =============================================================================

def is_image_url(text: str) -> bool:
    text = text.strip()
    return text.startswith("https://imgen.x.ai/") or text.startswith("https://api.x.ai/v1/images/")


async def get_conversation_thread(message) -> tuple[list[dict], list[str]]:
    """Walk back through reply chain. Returns (messages, message_ids)."""
    thread = []
    msg_ids = []
    current = message
    depth = 0

    while current and depth < MAX_CONVERSATION_DEPTH:
        content = strip_mentions(current.content)
        msg_ids.append(str(current.id))

        if content:
            if current.author == bot.user:
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
    return thread, msg_ids


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
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    content = strip_mentions(message.content)

    # Store ALL messages for RAG (even from non-grok channels)
    if content:
        channel_name = getattr(message.channel, "name", "DM")
        store_message(
            message_id=str(message.id),
            content=content,
            author=message.author.display_name,
            channel=channel_name,
            timestamp=message.created_at.isoformat(),
        )

    # Only respond in grok channels
    channel_name = getattr(message.channel, "name", "").lower()
    if "grok" not in channel_name:
        return

    # Only respond to mentions or replies
    is_mention = bot.user in message.mentions
    is_reply = is_reply_to_bot(message)

    if not is_mention and not is_reply:
        return

    if not content:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    user_id = message.author.id
    username = message.author.display_name

    # Handle image generation
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

    # Build conversation from reply chain
    conversation, thread_msg_ids = await get_conversation_thread(message)

    # Load user memory and find referenced users
    memory = load_memory()
    user_notes = get_user_notes(user_id, memory)

    full_conversation_text = " ".join(m["content"] for m in conversation)
    referenced_users = find_referenced_users(full_conversation_text, memory, exclude_user_id=user_id)

    # Retrieve relevant past messages via RAG
    rag_context = retrieve_relevant_context(content, exclude_ids=thread_msg_ids)

    # Build system prompt
    system = SYSTEM_PROMPT

    if user_notes:
        system += f"\n\nWhat you know about {username}: {user_notes}"

    if referenced_users:
        system += "\n\nOther people mentioned that you know about:"
        for ref_name, ref_notes in referenced_users.items():
            system += f"\n- {ref_name}: {ref_notes}"

    if rag_context:
        system += "\n\nRelevant past conversations from this server:"
        for ctx in rag_context[:5]:  # Limit to top 5
            system += f"\n- [{ctx['channel']}] {ctx['author']}: {ctx['content'][:200]}"

    messages = [{"role": "system", "content": system}] + conversation

    # Query Grok
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
