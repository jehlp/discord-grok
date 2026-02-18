import tempfile
from pathlib import Path

import discord

DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_file",
        "description": "Create a plain text file and upload it. Use for simple text-based files (scripts, configs, notes, code, markdown) that don't need compilation or special libraries. For office docs (.docx, .pptx, .xlsx), compiled code, or archives, use execute_code instead.",
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
        ctx.replied = True
        await ctx.message.reply(
            desc or f"Here's `{filename}`:",
            file=discord.File(str(tmp_path), filename=filename),
        )
    finally:
        tmp_path.unlink(missing_ok=True)
        Path(tmp_dir).rmdir()
    return f"File '{filename}' created and uploaded to the channel."
