import asyncio
import tempfile
from pathlib import Path

import discord

from ..memory import update_user_notes

DEFINITION = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": (
            "Execute shell commands to build, compile, or create files. "
            "Use when the user asks to make something that requires compilation (e.g. .jar, .exe, .o), "
            "packaging (e.g. .zip, .tar.gz), or document generation (e.g. .docx, .pptx, .xlsx). "
            "You can write source files and run build commands. "
            "Available tools: Python 3.12, gcc/g++, Java (javac/jar), Node.js, zip/tar, "
            "and Python libraries: python-docx, python-pptx, openpyxl. "
            "For office docs, write a Python script that uses these libraries, then run it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": (
                        "A bash script to execute. Write files, compile, package — whatever is needed. "
                        "The final artifact to upload should be written to /tmp/output/. "
                        "Example: echo 'public class Hi { public static void main(String[] a) { System.out.println(\"Hello\"); }}' > Hi.java && javac Hi.java && jar cfe /tmp/output/hello.jar Hi Hi.class"
                    ),
                },
                "upload_filename": {
                    "type": "string",
                    "description": "Filename for the uploaded artifact (e.g. 'hello.jar', 'report.docx'). If not specified, uploads all files in /tmp/output/.",
                },
                "description": {
                    "type": "string",
                    "description": "A brief message to send with the file.",
                },
            },
            "required": ["script"],
        },
    },
}

TIMEOUT_SECONDS = 30


async def handle(ctx, args):
    script = args.get("script", "")
    upload_filename = args.get("upload_filename")
    desc = args.get("description", "")

    # Create output directory
    output_dir = Path("/tmp/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Clean any previous output
    for f in output_dir.iterdir():
        if f.is_file():
            f.unlink()

    # Create a temp working directory for the build
    work_dir = tempfile.mkdtemp(prefix="grok_build_")

    # Run the script
    try:
        proc = await asyncio.create_subprocess_shell(
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        await ctx.message.reply("Build timed out after 30 seconds.")
        return "[build timed out]"
    except Exception as e:
        await ctx.message.reply(f"Build failed: {e}")
        return f"[build error: {e}]"

    if proc.returncode != 0:
        error_output = stderr.decode(errors="replace")[:1500]
        await ctx.message.reply(f"Build failed (exit {proc.returncode}):\n```\n{error_output}\n```")
        return f"[build failed: exit {proc.returncode}]"

    # Find files to upload
    output_files = list(output_dir.iterdir())
    if not output_files:
        # Maybe the output is just stdout
        stdout_text = stdout.decode(errors="replace")[:2000]
        if stdout_text.strip():
            await ctx.message.reply(f"```\n{stdout_text}\n```")
            return stdout_text
        await ctx.message.reply("Build completed but no output files were produced.")
        return "[no output]"

    # Upload files
    if upload_filename:
        # Upload specific file
        target = output_dir / upload_filename
        if target.exists():
            output_files = [target]
        # else upload all

    discord_files = []
    for f in output_files:
        if f.is_file() and f.stat().st_size <= 25_000_000:  # Discord 25MB limit
            discord_files.append(discord.File(str(f), filename=f.name))

    if discord_files:
        await ctx.message.reply(
            desc or f"Here you go:",
            files=discord_files,
        )
    else:
        await ctx.message.reply("Output files were too large to upload (>25MB).")

    reply = desc or f"[uploaded {', '.join(f.name for f in output_files)}]"
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
