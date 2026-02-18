from datetime import timedelta

import discord

from ..memory import update_user_notes

DEFINITION = {
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
}


async def handle(ctx, args):
    question = args.get("question", "Poll")[:300]
    answers = args.get("answers", ["Yes", "No"])[:10]
    duration = max(1, min(168, args.get("duration_hours", 24)))
    poll = discord.Poll(
        question=question,
        duration=timedelta(hours=duration),
    )
    for answer in answers:
        poll.add_answer(text=answer[:55])
    await ctx.message.channel.send(poll=poll)
    reply = f"[created poll: {question}]"
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
