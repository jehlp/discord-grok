import ast
import re

import aiohttp
from pathlib import Path

from .config import ALLOWED_TEXT_EXTENSIONS, IMAGE_EXTENSIONS, MAX_ATTACHMENT_SIZE


def format_api_error(e: Exception) -> str:
    """Return a clean, user-facing message for API errors."""
    raw = str(e)

    # Extract HTTP status code (e.g. "Error code: 403 - ...")
    code_match = re.search(r"Error code:\s*(\d+)", raw)
    status = int(code_match.group(1)) if code_match else None

    # Try to pull the body dict that follows the " - "
    body_str = re.sub(r"^.*?Error code:\s*\d+\s*-\s*", "", raw, count=1)
    error_detail = None
    try:
        body = ast.literal_eval(body_str)
        error_detail = body.get("error") or body.get("message") or body.get("code")
    except Exception:
        pass

    # Map well-known statuses to friendly explanations
    if status == 403:
        if error_detail and "content violates" in error_detail.lower():
            return (
                "Your request was blocked because the content violates the AI provider's "
                "usage guidelines. Try rephrasing or removing any policy-violating parts."
            )
        return (
            "Your request was denied — the account or API key doesn't have permission "
            "to perform this operation. Contact an admin if this is unexpected."
        )
    if status == 429:
        return "Rate limit hit. Too many requests in a short window — wait a moment and try again."
    if status in (500, 502, 503):
        return "The AI service is temporarily unavailable or overloaded. Try again in a few seconds."
    if status == 400:
        detail = f": {error_detail}" if error_detail else ""
        return f"Bad request{detail}. The message may be malformed or too long."
    if status == 401:
        return "Authentication failed. The API key is invalid or expired — check the bot config."

    # Generic fallback: show status + first line of error detail if available
    if status and error_detail:
        return f"API error {status}: {error_detail}"
    if status:
        return f"API error {status}. Check the logs for details."
    return f"Unexpected error: {raw[:200]}"


def strip_mentions(text: str) -> str:
    return re.sub(r"<@!?\d+>", "", text).strip()


def resolve_mentions(text: str, guild) -> str:
    """Replace <@123456> mention tags with @displayname so the model can see who was pinged."""
    if not guild:
        return strip_mentions(text)

    def replace_mention(match):
        user_id = int(match.group(1))
        member = guild.get_member(user_id)
        if member:
            return f"@{member.display_name}"
        return match.group(0)

    return re.sub(r"<@!?(\d+)>", replace_mention, text).strip()


async def read_attachments(attachments: list) -> tuple[list[dict], list[str]]:
    """Read text file attachments and collect image URLs. Returns (text_files, image_urls)."""
    results = []
    image_urls = []
    async with aiohttp.ClientSession() as session:
        for attachment in attachments:
            filename = attachment.filename.lower()
            ext = Path(filename).suffix

            # Check for image attachments
            if ext in IMAGE_EXTENSIONS:
                image_urls.append(attachment.url)
                continue

            # Check if it's a readable text file
            if ext not in ALLOWED_TEXT_EXTENSIONS:
                continue

            # Check file size
            if attachment.size > MAX_ATTACHMENT_SIZE:
                results.append({
                    "filename": attachment.filename,
                    "content": f"[File too large: {attachment.size:,} bytes, max {MAX_ATTACHMENT_SIZE:,}]"
                })
                continue

            try:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        results.append({
                            "filename": attachment.filename,
                            "content": content
                        })
            except Exception as e:
                results.append({
                    "filename": attachment.filename,
                    "content": f"[Failed to read: {e}]"
                })
    return results, image_urls


def sanitize_reply(text: str, allowed_user_id: int) -> str:
    # Remove @everyone and @here
    text = re.sub(r"@everyone", "", text)
    text = re.sub(r"@here", "", text)
    # Remove role pings <@&role_id>
    text = re.sub(r"<@&\d+>", "", text)
    # Only allow pinging the user who invoked the bot
    def replace(match):
        return match.group(0) if match.group(1) == str(allowed_user_id) else ""
    return re.sub(r"<@!?(\d+)>", replace, text)


async def send_reply(message, text: str):
    if len(text) <= 2000:
        await message.reply(text)
        return

    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
    for i, chunk in enumerate(chunks):
        if i == 0:
            await message.reply(chunk)
        else:
            await message.channel.send(chunk)
