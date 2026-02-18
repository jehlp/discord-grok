import os
import json
import re
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
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
SESSION_TTL_SECONDS = 1800  # 30 minutes
MAX_ATTACHMENT_SIZE = 100_000  # 100KB max for text files
ALLOWED_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".csv", ".xml", ".sh", ".bash", ".zsh", ".c", ".cpp",
    ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".log",
    ".ini", ".cfg", ".conf", ".env", ".gitignore", ".dockerfile",
}

SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry, sardonic humor. Skip the cheerful platitudes.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- You find performative enthusiasm annoying. Be real.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. You also have knowledge of past conversations in this server."""


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
# Per-User Session Store (in-memory)
# =============================================================================

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


def retrieve_relevant_context(query: str, exclude_ids: list[str] = None, min_distance: float = 0.25) -> list[dict]:
    """Retrieve relevant past messages for context, filtering by distance threshold."""
    try:
        results = message_collection.query(
            query_texts=[query],
            n_results=RAG_RESULTS,
            include=["documents", "metadatas", "distances"],
        )

        context = []
        if results and results["documents"] and results["documents"][0]:
            distances = results.get("distances", [[]])[0]
            for i, doc in enumerate(results["documents"][0]):
                msg_id = results["ids"][0][i] if results["ids"] else None
                if exclude_ids and msg_id in exclude_ids:
                    continue
                # Filter out low-relevance matches (higher distance = less relevant)
                if distances and i < len(distances) and distances[i] > (1.0 - min_distance):
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
# API Retry Logic
# =============================================================================

async def with_retry(func, *args, max_retries=3, **kwargs):
    """Run a function with exponential backoff retry on 503 errors."""
    for attempt in range(max_retries):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as e:
            if "503" in str(e) or "capacity" in str(e).lower():
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1  # 1s, 3s, 5s
                    await asyncio.sleep(wait_time)
                    continue
            raise
    raise Exception("Max retries exceeded")


# =============================================================================
# Search & Image Logic
# =============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current or recent information. Use when answering requires up-to-date data: news, prices, weather, scores, recent events, or anything you're unsure about.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text description. Use when the user asks to create, draw, render, or generate an image or picture.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Description of the image to generate"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_all_users",
            "description": "Get notes about all known users in this Discord server. Use when the question involves rankings, comparisons between members, or asks about everyone or the whole server.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def get_response_text(response) -> str:
    for item in response.output:
        if hasattr(item, "content"):
            for block in item.content:
                if hasattr(block, "text"):
                    return block.text
    return ""


async def query_with_search(messages: list[dict]) -> str:
    input_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
    response = await with_retry(
        xai.responses.create,
        model=MODEL,
        input=input_msgs,
        tools=[{"type": "web_search"}],
    )
    return get_response_text(response)


async def generate_image(prompt: str) -> str:
    response = await with_retry(
        xai.images.generate, model=IMAGE_MODEL, prompt=prompt
    )
    return response.data[0].url


# =============================================================================
# Conversation Threading & Context Building
# =============================================================================

def is_image_url(text: str) -> bool:
    text = text.strip()
    return text.startswith("https://imgen.x.ai/") or text.startswith("https://api.x.ai/v1/images/")


async def get_reply_chain(message) -> tuple[list[dict], list[str]]:
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


async def get_ambient_context(channel, user_id: int) -> str:
    """Fetch recent messages from other users for ambient channel awareness."""
    ambient = []
    try:
        async for msg in channel.history(limit=15):
            if msg.author.bot or msg.author.id == user_id:
                continue
            content = strip_mentions(msg.content)
            if not content:
                continue
            ambient.append(f"- {msg.author.display_name}: {content[:150]}")
            if len(ambient) >= 5:
                break
    except Exception:
        pass

    if not ambient:
        return ""

    ambient.reverse()  # chronological order
    return "\n\nRecent channel activity (for context, not directed at you):\n" + "\n".join(ambient)


async def build_context(message) -> tuple[list[dict], list[str]]:
    """Build conversation context. Returns (messages, msg_ids).

    Priority: reply chain > per-user session > fresh start.
    """
    user_id = message.author.id
    has_reply = message.reference and message.reference.message_id

    if has_reply:
        conversation, msg_ids = await get_reply_chain(message)
        return conversation, msg_ids

    # No reply chain — use session if available
    session = get_session(user_id)
    if session and session["messages"]:
        conversation = list(session["messages"])
        # Add the current message
        content = strip_mentions(message.content)
        if content:
            conversation.append({"role": "user", "content": content})
        return conversation, [str(message.id)]

    # No session either — fresh start
    conversation = []
    content = strip_mentions(message.content)
    if content:
        conversation.append({"role": "user", "content": content})
    return conversation, [str(message.id)]


def is_reply_to_bot(message) -> bool:
    if not message.reference or not message.reference.resolved:
        return False
    return message.reference.resolved.author == bot.user


# =============================================================================
# Helpers
# =============================================================================

def strip_mentions(text: str) -> str:
    return re.sub(r"<@!?\d+>", "", text).strip()


async def read_attachments(attachments: list) -> list[dict]:
    """Read text file attachments and return their contents."""
    results = []
    async with aiohttp.ClientSession() as session:
        for attachment in attachments:
            filename = attachment.filename.lower()
            ext = Path(filename).suffix

            # Check if it's a readable text file
            if ext not in ALLOWED_TEXT_EXTENSIONS:
                continue

            # Check file size
            if attachment.size > MAX_ATTACHMENT_SIZE:
                results.append({
                    "filename": attachment.filename,
                    "content": f"[File too large: {attachment.size:,} bytes, max {MAX_ATTACHMENT_SIZE:,}]"
                })
                continue

            try:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        results.append({
                            "filename": attachment.filename,
                            "content": content
                        })
            except Exception as e:
                results.append({
                    "filename": attachment.filename,
                    "content": f"[Failed to read: {e}]"
                })
    return results


def sanitize_reply(text: str, allowed_user_id: int) -> str:
    # Remove @everyone and @here
    text = re.sub(r"@everyone", "", text)
    text = re.sub(r"@here", "", text)
    # Remove role pings <@&role_id>
    text = re.sub(r"<@&\d+>", "", text)
    # Only allow pinging the user who invoked the bot
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
    bot.loop.create_task(cleanup_sessions())


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

    # Read any attached files
    attachments_content = await read_attachments(message.attachments)

    if not content and not attachments_content:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    user_id = message.author.id
    username = message.author.display_name

    # Build conversation from reply chain or session
    conversation, thread_msg_ids = await build_context(message)

    # Append attachment content to the last user message
    if attachments_content and conversation:
        attachment_text = "\n\n--- Attached Files ---"
        for att in attachments_content:
            attachment_text += f"\n\n### {att['filename']}\n```\n{att['content']}\n```"
        # Find the last user message (should be the current one)
        for i in range(len(conversation) - 1, -1, -1):
            if conversation[i]["role"] == "user":
                conversation[i]["content"] += attachment_text
                break

    # Load user memory and find referenced users
    memory = load_memory()
    user_notes = get_user_notes(user_id, memory)

    # Extract explicitly mentioned user IDs from raw message (before stripping)
    mentioned_ids = extract_mentioned_user_ids(message.content)

    full_conversation_text = " ".join(m["content"] for m in conversation)
    referenced_users = find_referenced_users(full_conversation_text, memory, exclude_user_id=user_id, mentioned_ids=mentioned_ids)

    # Retrieve relevant past messages via RAG (with relevance filtering)
    rag_context = retrieve_relevant_context(content, exclude_ids=thread_msg_ids)

    # Fetch ambient channel context (recent messages from other users)
    ambient = await get_ambient_context(message.channel, user_id)

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

    if ambient:
        system += ambient

    messages = [{"role": "system", "content": system}] + conversation

    # Query Grok with tool definitions — the model decides what to invoke
    async with message.channel.typing():
        try:
            response = await with_retry(
                xai.chat.completions.create,
                model=MODEL,
                messages=messages,
                tools=TOOLS,
            )

            choice = response.choices[0]
            reply = None

            # No tool calls — straightforward text response
            if not choice.message.tool_calls:
                reply = choice.message.content
                reply = sanitize_reply(reply, user_id)
                await send_reply(message, reply)
                await update_user_notes(user_id, username, content, memory)
            else:
                # Handle each tool call
                for tool_call in choice.message.tool_calls:
                    name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)

                    if name == "generate_image":
                        if is_image_rate_limited(user_id):
                            remaining = get_image_cooldown_remaining(user_id)
                            minutes = remaining // 60
                            seconds = remaining % 60
                            await message.reply(f"Image cooldown. Try again in {minutes}m {seconds}s.")
                        else:
                            image_url = await generate_image(args.get("prompt", content))
                            await message.reply(image_url)
                            record_image_request(user_id)
                        # Don't persist image requests to session
                        return

                    if name == "web_search":
                        reply = await query_with_search(messages)
                        reply = sanitize_reply(reply, user_id)
                        await send_reply(message, reply)
                        await update_user_notes(user_id, username, content, memory)
                        break

                    if name == "get_all_users":
                        # Inject all user notes and re-query without tools
                        system += "\n\nAll people you know about in this server:"
                        for uid, data in memory.items():
                            if uid == str(user_id):
                                continue
                            uname = data.get("username", "Unknown")
                            unotes = data.get("notes", "")
                            if unotes:
                                system += f"\n- {uname}: {unotes}"
                        messages[0] = {"role": "system", "content": system}
                        response2 = await with_retry(
                            xai.chat.completions.create,
                            model=MODEL,
                            messages=messages,
                        )
                        reply = response2.choices[0].message.content
                        reply = sanitize_reply(reply, user_id)
                        await send_reply(message, reply)
                        await update_user_notes(user_id, username, content, memory)
                        break

            # Persist conversation to session
            if reply:
                # conversation already includes the current user message (from build_context)
                conversation.append({"role": "assistant", "content": reply})
                update_session(user_id, conversation)

        except Exception as e:
            await message.reply(f"Something broke: {e}")


# =============================================================================
# Entry
# =============================================================================

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
