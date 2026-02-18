import tempfile
from pathlib import Path

import discord

from ..memory import update_user_notes

DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_file",
        "description": "Create a file and upload it to the chat. Use when the user asks you to make, write, or create a file, script, document, config, or any downloadable content.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "The filename including extension (e.g. 'script.py', 'notes.txt', 'config.yaml')"},
                "content": {"type": "string", "description": "The full content of the file"},
                "description": {"type": "string", "description": "A brief message to send along with the file"},
            },
            "required": ["filename", "content"],
        },
    },
}


async def handle(ctx, args):
    filename = args.get("filename", "file.txt")
    file_content = args.get("content", "")
    desc = args.get("description", "")
    # Write to temp file and upload
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir) / filename
    tmp_path.write_text(file_content)
    try:
        await ctx.message.reply(
            desc or f"Here's `{filename}`:",
            file=discord.File(str(tmp_path), filename=filename),
        )
    finally:
        tmp_path.unlink(missing_ok=True)
        Path(tmp_dir).rmdir()
    reply = desc or f"[uploaded {filename}]"
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
