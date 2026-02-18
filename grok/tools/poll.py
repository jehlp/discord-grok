from datetime import timedelta

import discord

DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_poll",
        "description": "Create a poll. Use when someone wants a vote, poll, survey, 'let's settle this', 'what does everyone think', or any situation where a group decision would help.",
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
    ctx.replied = True
    await ctx.message.channel.send(poll=poll)
    return f"Poll created: {question}"
