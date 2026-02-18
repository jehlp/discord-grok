from ..config import MODEL
from ..clients import xai
from ..api import with_retry
from ..helpers import sanitize_reply, send_reply
from ..memory import update_user_notes

DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_all_users",
        "description": "Get notes about all known users in this Discord server. Use when the question involves rankings, comparisons between members, or asks about everyone or the whole server.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


async def handle(ctx, args):
    # Inject all user notes and re-query without tools
    system = ctx.system
    system += "\n\nAll people you know about in this server:"
    for uid, data in ctx.memory.items():
        if uid == str(ctx.user_id):
            continue
        uname = data.get("username", "Unknown")
        unotes = data.get("notes", "")
        if unotes:
            system += f"\n\n**{uname}**\n{unotes}"
    messages = [{"role": "system", "content": system}] + ctx.conversation
    response = await with_retry(
        xai.chat.completions.create,
        model=MODEL,
        messages=messages,
    )
    reply = response.choices[0].message.content
    reply = sanitize_reply(reply, ctx.user_id)
    await send_reply(ctx.message, reply)
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
