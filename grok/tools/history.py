import re
from datetime import datetime, timedelta, timezone

from ..config import MODEL
from ..clients import xai
from ..api import with_retry
from ..helpers import strip_mentions, sanitize_reply, send_reply
from ..memory import update_user_notes

DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_chat_history",
        "description": "Search channel chat history. Use when someone mentions old messages, past conversations, 'remember when', 'who said', 'find that message', 'scroll back', or anything about what was said before.",
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
}


async def handle(ctx, args):
    objective = args.get("objective", "find interesting messages")
    hours_back = max(1, min(720, args.get("hours_back", 24)))
    max_msgs = max(10, min(500, args.get("max_messages", 200)))
    after_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    # Fetch channel history
    history_lines = []
    history_msgs = {}  # index -> message object for pinning
    msg_index = 0
    async for msg in ctx.message.channel.history(limit=max_msgs, after=after_time, oldest_first=True):
        if msg.author.bot:
            continue
        msg_content = strip_mentions(msg.content)
        if not msg_content:
            continue
        msg_index += 1
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
        line = f"#{msg_index} [{timestamp}] {msg.author.display_name}: {msg_content[:300]}"
        history_lines.append(line)
        history_msgs[str(msg_index)] = msg

    if not history_lines:
        reply = f"No messages found in the last {hours_back} hours."
        await send_reply(ctx.message, reply)
        await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
        return reply

    # Build a focused prompt with the history
    history_block = "\n".join(history_lines)
    search_system = ctx.system + f"\n\nYou searched the channel history ({len(history_lines)} messages from the last {hours_back}h). Your objective: {objective}\n\nHere are the messages:\n\n{history_block}"
    search_system += "\n\nDo NOT include message numbers, IDs, or internal formatting in your response — just talk naturally."
    search_system += " If you need to pin a message, add [PIN:#N] at the very end of your response (where N is the message number). Only pin if explicitly asked to."

    search_messages = [{"role": "system", "content": search_system}] + ctx.conversation
    response = await with_retry(
        xai.chat.completions.create,
        model=MODEL,
        messages=search_messages,
    )
    reply = response.choices[0].message.content

    # Check for pin directives
    pin_match = re.search(r'\[PIN:#?(\d+)\]', reply)
    if pin_match:
        pin_idx = pin_match.group(1)
        reply = reply.replace(pin_match.group(0), "").strip()
        if pin_idx in history_msgs:
            try:
                await history_msgs[pin_idx].pin()
            except Exception as e:
                print(f"Failed to pin message #{pin_idx}: {e}")

    reply = sanitize_reply(reply, ctx.user_id)
    await send_reply(ctx.message, reply)
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
