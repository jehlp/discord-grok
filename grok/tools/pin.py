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
        return "Message pinned successfully."
    except Exception as e:
        return f"Failed to pin message: {e}"
