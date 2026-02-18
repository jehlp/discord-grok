import asyncio
import os
import signal
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

EXECUTE_COOLDOWN_SECONDS = 600  # 10 minutes between executions per user
last_execute_request: dict[int, datetime] = {}

DEFINITION = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": (
            "Build, compile, or generate any file that needs code execution. "
            "Covers: .jar, .exe, .o, .zip, .tar.gz, .docx, .xlsx, compiled programs, archives, etc. "
            "For PowerPoints/slides, use create_presentation instead. "
            "Available: Python 3.12, gcc/g++, Java (javac/jar), Node.js, zip/tar, "
            "python-docx, openpyxl. "
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
        f"ulimit -v {MEM_LIMIT_BYTES // 1024} 2>/dev/null || true\n"
        f"ulimit -f {MAX_OUTPUT_SIZE // 512} 2>/dev/null || true\n"
        "ulimit -t 30 2>/dev/null || true\n"
        "ulimit -u 64 2>/dev/null || true\n"
        f"{script}\n"
    )


async def handle(ctx, args):
    # Rate limit check
    now = datetime.now(timezone.utc)
    last = last_execute_request.get(ctx.user_id)
    if last and (now - last) < timedelta(seconds=EXECUTE_COOLDOWN_SECONDS):
        remaining = EXECUTE_COOLDOWN_SECONDS - int((now - last).total_seconds())
        minutes = remaining // 60
        seconds = remaining % 60
        msg = f"Build cooldown — try again in {minutes}m {seconds}s."
        print(f"[execute_code] RATE LIMITED user {ctx.user_id}: {msg}")
        ctx.replied = True
        await ctx.message.reply(msg)
        return msg

    script = args.get("script", "")
    upload_filename = args.get("upload_filename")

    last_execute_request[ctx.user_id] = now

    # Create output directory
    output_dir = Path("/tmp/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    for f in output_dir.iterdir():
        if f.is_file():
            f.unlink()

    work_dir = tempfile.mkdtemp(prefix="grok_build_")

    # Write the sandboxed script
    script_path = Path(work_dir) / "_run.sh"
    script_path.write_text(_build_sandboxed_script(script))
    script_path.chmod(0o755)

    # Run in its own process group
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
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        return "Build timed out (30s limit). Script took too long — simplify or reduce scope."
    except Exception as e:
        return f"Build failed to start: {e}"

    stdout_text = stdout.decode(errors="replace")[:2000]
    stderr_text = stderr.decode(errors="replace")[:2000]

    if proc.returncode != 0:
        print(f"[execute_code] FAILED (exit {proc.returncode})\nstderr: {stderr_text[:500]}")
        if proc.returncode == -9 or proc.returncode == 137:
            return "Build killed — hit resource limits (CPU or memory). Simplify the task."
        return f"Build failed (exit {proc.returncode}):\n{stderr_text[:1500]}"

    print(f"[execute_code] OK. stdout: {stdout_text[:200]}")
    if stderr_text.strip():
        print(f"[execute_code] stderr: {stderr_text[:200]}")

    # Find files to upload — check /tmp/output/ first, then fall back to work_dir
    output_files = [f for f in output_dir.iterdir() if f.is_file()]
    if not output_files:
        # Check if script wrote files to work_dir instead
        work_files = [
            f for f in Path(work_dir).iterdir()
            if f.is_file() and f.name != "_run.sh"
            and not f.name.endswith((".java", ".c", ".cpp", ".h", ".py", ".js", ".sh"))
        ]
        if work_files:
            print(f"[execute_code] No files in /tmp/output, found in work_dir: {[f.name for f in work_files]}")
            output_files = work_files

    if not output_files:
        print(f"[execute_code] No output files found anywhere")
        if stdout_text.strip():
            return f"Build completed. Output:\n{stdout_text}"
        return "Build completed but no output files were produced. Make sure to write output to /tmp/output/."

    if upload_filename:
        target = output_dir / upload_filename
        if target.exists():
            output_files = [target]

    # Upload files to Discord
    discord_files = []
    for f in output_files:
        if f.is_file() and f.stat().st_size <= 25_000_000:
            discord_files.append(discord.File(str(f), filename=f.name))

    if discord_files:
        filenames = ", ".join(f.name for f in output_files)
        ctx.replied = True
        await ctx.message.reply(
            "Here you go:",
            files=discord_files,
        )
        return f"Files created and uploaded: {filenames}"
    else:
        return "Output files were too large to upload (>25MB Discord limit)."
