from ..api import query_with_search
from ..helpers import sanitize_reply, send_reply
from ..memory import update_user_notes

DEFINITION = {
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
}


async def handle(ctx, args):
    reply = await query_with_search(ctx.messages)
    reply = sanitize_reply(reply, ctx.user_id)
    await send_reply(ctx.message, reply)
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
