import asyncio
import os
import signal
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

PPTX_COOLDOWN_SECONDS = 600  # 10 minutes per user
last_pptx_request: dict[int, datetime] = {}

DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_presentation",
        "description": (
            "Create a PowerPoint presentation. Use whenever someone wants slides, a deck, "
            "a presentation, or a pptx. Uses a pre-built professional template — just provide "
            "the slide content as a Python script. "
            "Available slide types: add_title_slide(title, subtitle), "
            "add_section_slide(heading, description), "
            "add_content_slide(title, [prose points]), "
            "add_two_column_slide(title, left_title, left_points, right_title, right_points), "
            "add_quote_slide(quote, attribution), "
            "add_closing_slide(headline, subtext). "
            "Write INSIGHTFUL PROSE for each point — not lazy bullet fragments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": (
                        "A Python script (NOT bash) that builds the deck. It will be run with "
                        "the Deck class already imported. Example:\n"
                        "deck = Deck('AI in Healthcare')\n"
                        "deck.add_title_slide('AI in Healthcare', 'Transforming Patient Outcomes')\n"
                        "deck.add_content_slide('The Current Landscape', [\n"
                        "    'Hospital adoption of AI diagnostics grew 340% between 2020-2024',\n"
                        "    'Early detection algorithms now match radiologist accuracy in 12 cancer types',\n"
                        "])\n"
                        "deck.save('/tmp/output/presentation.pptx')"
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename (default: presentation.pptx)",
                },
            },
            "required": ["script"],
        },
    },
}

TIMEOUT_SECONDS = 30


async def handle(ctx, args):
    # Rate limit
    now = datetime.now(timezone.utc)
    last = last_pptx_request.get(ctx.user_id)
    if last and (now - last) < timedelta(seconds=PPTX_COOLDOWN_SECONDS):
        remaining = PPTX_COOLDOWN_SECONDS - int((now - last).total_seconds())
        minutes = remaining // 60
        seconds = remaining % 60
        msg = f"Presentation cooldown — try again in {minutes}m {seconds}s."
        print(f"[create_presentation] RATE LIMITED user {ctx.user_id}: {msg}")
        if not ctx.replied:
            ctx.replied = True
            await ctx.message.reply(msg)
        return msg

    script = args.get("script", "")
    filename = args.get("filename", "presentation.pptx")

    last_pptx_request[ctx.user_id] = now

    # Create output directory
    output_dir = Path("/tmp/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    for f in output_dir.iterdir():
        if f.is_file():
            f.unlink()

    # Wrap the script with the Deck import
    wrapped = (
        "import sys; sys.path.insert(0, '/app')\n"
        "from grok.pptx_template import Deck\n"
        "from pathlib import Path\n"
        "Path('/tmp/output').mkdir(parents=True, exist_ok=True)\n\n"
        f"{script}\n"
    )

    work_dir = tempfile.mkdtemp(prefix="grok_pptx_")
    script_path = Path(work_dir) / "build_pptx.py"
    script_path.write_text(wrapped)

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            preexec_fn=os.setsid,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        return "Presentation build timed out (30s limit)."
    except Exception as e:
        return f"Presentation build failed: {e}"

    stderr_text = stderr.decode(errors="replace")
    if proc.returncode != 0:
        print(f"[create_presentation] FAILED: {stderr_text[:500]}")
        return f"Build failed:\n{stderr_text[:1500]}"

    # Find and upload the pptx
    output_files = [f for f in output_dir.iterdir() if f.is_file()]
    if not output_files:
        # Check work dir
        output_files = [f for f in Path(work_dir).iterdir()
                       if f.suffix == ".pptx"]

    if not output_files:
        print(f"[create_presentation] No output files found")
        return "Build completed but no .pptx file was produced. Make sure to call deck.save('/tmp/output/presentation.pptx')."

    discord_files = []
    for f in output_files:
        if f.is_file() and f.stat().st_size <= 25_000_000:
            discord_files.append(discord.File(str(f), filename=filename))

    if discord_files:
        ctx.replied = True
        await ctx.message.reply("Here you go:", files=discord_files)
        return f"Presentation '{filename}' created and uploaded."
    else:
        return "Presentation file was too large to upload (>25MB)."
