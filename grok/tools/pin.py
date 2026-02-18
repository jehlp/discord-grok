from ..config import MODEL
from ..clients import xai
from ..api import with_retry
from ..helpers import sanitize_reply, send_reply
from ..memory import update_user_notes

DEFINITION = {
    "type": "function",
    "function": {
        "name": "pin_message",
        "description": "Pin the user's message to the channel. Use VERY rarely — only when a message is truly exceptional, hilarious, outlandish, or legendary. Most messages don't deserve a pin. Maybe 1 in 50 at most.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


async def handle(ctx, args):
    try:
        await ctx.message.pin()
    except Exception as e:
        print(f"Failed to pin message: {e}")
    # Re-query without the pin tool to get a text response too
    response = await with_retry(
        xai.chat.completions.create,
        model=MODEL,
        messages=ctx.messages,
    )
    reply = response.choices[0].message.content
    reply = sanitize_reply(reply, ctx.user_id)
    await send_reply(ctx.message, reply)
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
