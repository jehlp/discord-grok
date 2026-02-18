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
    lines = []
    for uid, data in ctx.memory.items():
        if uid == str(ctx.user_id):
            continue
        uname = data.get("username", "Unknown")
        unotes = data.get("notes", "")
        if unotes:
            lines.append(f"{uname}: {unotes}")

    if not lines:
        return "No other users known yet."

    return "All known users in this server:\n\n" + "\n\n".join(lines)
