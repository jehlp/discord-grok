from .config import MAX_CONVERSATION_DEPTH
from .clients import bot
from .sessions import get_session
from .helpers import strip_mentions, resolve_mentions


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
            except Exception:
                break
        else:
            break

    thread.reverse()
    return thread, msg_ids


async def get_ambient_context(channel, user_id: int) -> str:
    """Fetch recent messages from other users for ambient channel awareness."""
    ambient = []
    try:
        async for msg in channel.history(limit=10):
            if msg.author.bot or msg.author.id == user_id:
                continue
            content = strip_mentions(msg.content)
            if not content:
                continue
            ambient.append(f"- {msg.author.display_name}: {content[:100]}")
            if len(ambient) >= 3:
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

    # No reply chain -- use session if available
    session = get_session(user_id)
    if session and session["messages"]:
        conversation = list(session["messages"])
        # Add the current message
        content = resolve_mentions(message.content, message.guild)
        if content:
            labeled = f"[{message.author.display_name}] {content}"
            conversation.append({"role": "user", "content": labeled})
        return conversation, [str(message.id)]

    # No session either -- fresh start
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
