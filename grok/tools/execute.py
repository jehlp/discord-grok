import asyncio
import os
import signal
import tempfile
from pathlib import Path

import discord

from ..memory import update_user_notes

DEFINITION = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": (
            "Build, compile, or generate any file that needs code execution. Use this tool broadly — "
            "whenever someone wants a program, document, slideshow, spreadsheet, archive, or any "
            "non-trivial file created. Covers: .jar, .exe, .o, .zip, .tar.gz, .docx, .pptx, .xlsx, "
            "compiled programs, packaged projects, office documents, etc. "
            "Available: Python 3.12, gcc/g++, Java (javac/jar), Node.js, zip/tar, "
            "python-docx, python-pptx, openpyxl. "
            "For office docs: write a Python script using these libraries, then run it. "
            "NOT for heavy computation — refuse resource-hogging requests (huge prime searches, "
            "mining, stress tests, infinite loops). Limited to 30s CPU, 256MB RAM, 50MB disk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": (
                        "A BASH SHELL script (not raw Python/Java). Write source to files, then compile/run. "
                        "Output artifacts to /tmp/output/. "
                        "For Python: cat << 'PYEOF' > build.py\\n...python code...\\nPYEOF\\npython3 build.py "
                        "For Java: echo 'class Hi{...}' > Hi.java && javac Hi.java && jar cfe /tmp/output/hi.jar Hi Hi.class "
                        "For C: echo '...' > main.c && gcc -o /tmp/output/prog main.c"
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
MAX_OUTPUT_SIZE = 50_000_000  # 50MB disk limit
MEM_LIMIT_BYTES = 256 * 1024 * 1024  # 256MB


def _build_sandboxed_script(script: str) -> str:
    """Wrap user script with resource limits via ulimit."""
    return (
        "#!/bin/bash\n"
        "set -e\n"
        f"ulimit -v {MEM_LIMIT_BYTES // 1024} 2>/dev/null || true\n"  # virtual memory (KB)
        f"ulimit -f {MAX_OUTPUT_SIZE // 512} 2>/dev/null || true\n"    # max file size (512-byte blocks)
        "ulimit -t 30 2>/dev/null || true\n"                           # CPU seconds
        "ulimit -u 64 2>/dev/null || true\n"                           # max processes (prevent fork bombs)
        f"{script}\n"
    )


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

    # Write the sandboxed script to a file
    script_path = Path(work_dir) / "_run.sh"
    script_path.write_text(_build_sandboxed_script(script))
    script_path.chmod(0o755)

    # Run the script in its own process group so we can kill the whole tree
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            preexec_fn=os.setsid,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # Kill the entire process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        await ctx.message.reply("Build timed out (30s limit). This tool is for creating files, not heavy computation.")
        return "[build timed out]"
    except Exception as e:
        await ctx.message.reply(f"Build failed: {e}")
        return f"[build error: {e}]"

    if proc.returncode != 0:
        error_output = stderr.decode(errors="replace")[:1500]
        # Detect resource limit kills
        if proc.returncode == -9 or proc.returncode == 137:
            await ctx.message.reply("Build killed — hit resource limits (CPU or memory). This tool is for creating files, not heavy computation.")
        else:
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
        target = output_dir / upload_filename
        if target.exists():
            output_files = [target]

    discord_files = []
    for f in output_files:
        if f.is_file() and f.stat().st_size <= 25_000_000:  # Discord 25MB limit
            discord_files.append(discord.File(str(f), filename=f.name))

    if discord_files:
        await ctx.message.reply(
            desc or "Here you go:",
            files=discord_files,
        )
    else:
        await ctx.message.reply("Output files were too large to upload (>25MB).")

    reply = desc or f"[uploaded {', '.join(f.name for f in output_files)}]"
    await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
    return reply
