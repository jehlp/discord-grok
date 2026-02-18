import os
import json
import re
import asyncio
import tempfile
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
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry humor and edgy quips are welcome, but use them sparingly. Lead with substance, season with wit.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- Don't try too hard to be funny. One good line beats three mediocre ones.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. You also have knowledge of past conversations in this server.

User messages are prefixed with [username] to show who's speaking. When multiple users are in a conversation, pay close attention to these labels. @mentions in messages show who was pinged."""


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
    {
        "type": "function",
        "function": {
            "name": "pin_message",
            "description": "Pin the user's message to the channel. Use VERY rarely — only when a message is truly exceptional, hilarious, outlandish, or legendary. Most messages don't deserve a pin. Maybe 1 in 50 at most.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a file and upload it to the chat. Use when the user asks you to make, write, or create a file, script, document, config, or any downloadable content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The filename including extension (e.g. 'script.py', 'notes.txt', 'config.yaml')"},
                    "content": {"type": "string", "description": "The full content of the file"},
                    "description": {"type": "string", "description": "A brief message to send along with the file"},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_poll",
            "description": "Create a poll in the channel. Use when the user asks to make a poll, vote, or survey — or when you think a poll would be fun or relevant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The poll question (max 300 chars)"},
                    "answers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of answer options (2-10 options, each max 55 chars)",
                    },
                    "duration_hours": {"type": "integer", "description": "How long the poll runs in hours (1-168, default 24)"},
                },
                "required": ["question", "answers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_chat_history",
            "description": "Search through the channel's chat history. Use when the user asks to look through, find, or search past messages. Can search by time range or message count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "description": "What you're looking for or trying to accomplish (e.g. 'find the funniest message', 'find messages about python')"},
                    "hours_back": {"type": "integer", "description": "How many hours back to search (default 24, max 720 which is 30 days)"},
                    "max_messages": {"type": "integer", "description": "Max number of messages to retrieve (default 200, max 500)"},
                },
                "required": ["objective"],
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
    guild = message.guild

    while current and depth < MAX_CONVERSATION_DEPTH:
        content = resolve_mentions(current.content, guild)
        msg_ids.append(str(current.id))

        if content:
            if current.author == bot.user:
                if is_image_url(content):
                    thread.append({"role": "assistant", "content": "[I generated an image]"})
                else:
                    thread.append({"role": "assistant", "content": content})
            else:
                # Label who's speaking so the model knows which user said what
                labeled = f"[{current.author.display_name}] {content}"
                thread.append({"role": "user", "content": labeled})

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
        content = resolve_mentions(message.content, message.guild)
        if content:
            labeled = f"[{message.author.display_name}] {content}"
            conversation.append({"role": "user", "content": labeled})
        return conversation, [str(message.id)]

    # No session either — fresh start
    conversation = []
    content = resolve_mentions(message.content, message.guild)
    if content:
        labeled = f"[{message.author.display_name}] {content}"
        conversation.append({"role": "user", "content": labeled})
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


def resolve_mentions(text: str, guild) -> str:
    """Replace <@123456> mention tags with @displayname so the model can see who was pinged."""
    if not guild:
        return strip_mentions(text)

    def replace_mention(match):
        user_id = int(match.group(1))
        member = guild.get_member(user_id)
        if member:
            return f"@{member.display_name}"
        return match.group(0)

    return re.sub(r"<@!?(\d+)>", replace_mention, text).strip()


async def read_attachments(attachments: list) -> tuple[list[dict], list[str]]:
    """Read text file attachments and collect image URLs. Returns (text_files, image_urls)."""
    results = []
    image_urls = []
    async with aiohttp.ClientSession() as session:
        for attachment in attachments:
            filename = attachment.filename.lower()
            ext = Path(filename).suffix

            # Check for image attachments
            if ext in IMAGE_EXTENSIONS:
                image_urls.append(attachment.url)
                continue

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
    return results, image_urls


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

    # Read any attached files and images
    attachments_content, image_urls = await read_attachments(message.attachments)

    if not content and not attachments_content and not image_urls:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    user_id = message.author.id
    username = message.author.display_name

    # Build conversation from reply chain or session
    conversation, thread_msg_ids = await build_context(message)

    # Append attachment content and images to the last user message
    if (attachments_content or image_urls) and conversation:
        # Find the last user message (should be the current one)
        for i in range(len(conversation) - 1, -1, -1):
            if conversation[i]["role"] == "user":
                # Append text file contents
                if attachments_content:
                    attachment_text = "\n\n--- Attached Files ---"
                    for att in attachments_content:
                        attachment_text += f"\n\n### {att['filename']}\n```\n{att['content']}\n```"
                    conversation[i]["content"] += attachment_text

                # Convert to multi-part content format for images
                if image_urls:
                    parts = [{"type": "text", "text": conversation[i]["content"]}]
                    for url in image_urls:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                    conversation[i]["content"] = parts
                break

    # Load user memory and find referenced users
    memory = load_memory()
    user_notes = get_user_notes(user_id, memory)

    # Extract explicitly mentioned user IDs from raw message (before stripping)
    mentioned_ids = extract_mentioned_user_ids(message.content)

    full_conversation_text = " ".join(
        m["content"] if isinstance(m["content"], str)
        else " ".join(p["text"] for p in m["content"] if p["type"] == "text")
        for m in conversation
    )
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

                    if name == "pin_message":
                        try:
                            await message.pin()
                        except Exception as e:
                            print(f"Failed to pin message: {e}")
                        # Re-query without the pin tool to get a text response too
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

                    if name == "web_search":
                        reply = await query_with_search(messages)
                        reply = sanitize_reply(reply, user_id)
                        await send_reply(message, reply)
                        await update_user_notes(user_id, username, content, memory)
                        break

                    if name == "create_poll":
                        question = args.get("question", "Poll")[:300]
                        answers = args.get("answers", ["Yes", "No"])[:10]
                        duration = max(1, min(168, args.get("duration_hours", 24)))
                        poll = discord.Poll(
                            question=question,
                            duration=timedelta(hours=duration),
                        )
                        for answer in answers:
                            poll.add_answer(text=answer[:55])
                        await message.channel.send(poll=poll)
                        reply = f"[created poll: {question}]"
                        await update_user_notes(user_id, username, content, memory)
                        break

                    if name == "search_chat_history":
                        objective = args.get("objective", "find interesting messages")
                        hours_back = max(1, min(720, args.get("hours_back", 24)))
                        max_msgs = max(10, min(500, args.get("max_messages", 200)))
                        after_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

                        # Fetch channel history
                        history_lines = []
                        history_msgs = {}  # id -> message object for pinning etc
                        async for msg in message.channel.history(limit=max_msgs, after=after_time, oldest_first=True):
                            if msg.author.bot:
                                continue
                            msg_content = strip_mentions(msg.content)
                            if not msg_content:
                                continue
                            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                            line = f"[{timestamp}] {msg.author.display_name} (msg_id:{msg.id}): {msg_content[:300]}"
                            history_lines.append(line)
                            history_msgs[str(msg.id)] = msg

                        if not history_lines:
                            reply = f"No messages found in the last {hours_back} hours."
                            await send_reply(message, reply)
                            await update_user_notes(user_id, username, content, memory)
                            break

                        # Build a focused prompt with the history
                        history_block = "\n".join(history_lines)
                        search_system = system + f"\n\nYou searched the channel history ({len(history_lines)} messages from the last {hours_back}h). Your objective: {objective}\n\nHere are the messages:\n\n{history_block}"
                        search_system += "\n\nIMPORTANT: If you want to pin a message, include its msg_id in your response like [PIN:msg_id]. Only pin if explicitly asked to."

                        search_messages = [{"role": "system", "content": search_system}] + conversation
                        response2 = await with_retry(
                            xai.chat.completions.create,
                            model=MODEL,
                            messages=search_messages,
                        )
                        reply = response2.choices[0].message.content

                        # Check for pin directives
                        pin_match = re.search(r'\[PIN:(\d+)\]', reply)
                        if pin_match:
                            pin_id = pin_match.group(1)
                            reply = reply.replace(pin_match.group(0), "").strip()
                            if pin_id in history_msgs:
                                try:
                                    await history_msgs[pin_id].pin()
                                except Exception as e:
                                    print(f"Failed to pin message {pin_id}: {e}")

                        reply = sanitize_reply(reply, user_id)
                        await send_reply(message, reply)
                        await update_user_notes(user_id, username, content, memory)
                        break

                    if name == "create_file":
                        filename = args.get("filename", "file.txt")
                        file_content = args.get("content", "")
                        desc = args.get("description", "")
                        # Write to temp file and upload
                        tmp_dir = tempfile.mkdtemp()
                        tmp_path = Path(tmp_dir) / filename
                        tmp_path.write_text(file_content)
                        try:
                            await message.reply(
                                desc or f"Here's `{filename}`:",
                                file=discord.File(str(tmp_path), filename=filename),
                            )
                        finally:
                            tmp_path.unlink(missing_ok=True)
                            Path(tmp_dir).rmdir()
                        reply = desc or f"[uploaded {filename}]"
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
                                system += f"\n\n**{uname}**\n{unotes}"
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

            # Persist conversation to session (strip image parts — URLs expire)
            if reply:
                session_msgs = []
                for msg in conversation:
                    if isinstance(msg["content"], list):
                        # Extract just the text parts from multi-part messages
                        text = " ".join(p["text"] for p in msg["content"] if p["type"] == "text")
                        session_msgs.append({"role": msg["role"], "content": text})
                    else:
                        session_msgs.append(msg)
                session_msgs.append({"role": "assistant", "content": reply})
                update_session(user_id, session_msgs)

        except Exception as e:
            await message.reply(f"Something broke: {e}")


# =============================================================================
# Entry
# =============================================================================

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
