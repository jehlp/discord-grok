from datetime import datetime, timedelta, timezone

from ..helpers import strip_mentions

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
    max_msgs = max(10, min(200, args.get("max_messages", 100)))
    after_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    # Fetch channel history
    history_lines = []
    msg_index = 0
    async for msg in ctx.message.channel.history(limit=max_msgs, after=after_time, oldest_first=True):
        if msg.author.bot:
            continue
        msg_content = strip_mentions(msg.content)
        if not msg_content:
            continue
        msg_index += 1
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
        line = f"[{timestamp}] {msg.author.display_name}: {msg_content[:150]}"
        history_lines.append(line)

    if not history_lines:
        return f"No messages found in the last {hours_back} hours."

    history_block = "\n".join(history_lines)
    return f"Search objective: {objective}\n{len(history_lines)} messages from the last {hours_back}h:\n\n{history_block}"
