from datetime import datetime, timedelta, timezone

from ..api import generate_image as gen_image
from ..config import IMAGE_RATE_LIMIT_SECONDS

DEFINITION = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": "Generate an image. Use whenever someone wants a picture, drawing, render, meme, artwork, visualization, or anything visual created. Casual phrasing counts — 'draw me', 'make a pic of', 'show me what X looks like', etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Description of the image to generate"},
            },
            "required": ["prompt"],
        },
    },
}

# In-memory rate limiting
last_image_request: dict[int, datetime] = {}


def is_image_rate_limited(user_id: int) -> bool:
    if user_id not in last_image_request:
        return False
    elapsed = datetime.now(timezone.utc) - last_image_request[user_id]
    return elapsed < timedelta(seconds=IMAGE_RATE_LIMIT_SECONDS)


def get_image_cooldown_remaining(user_id: int) -> int:
    elapsed = datetime.now(timezone.utc) - last_image_request[user_id]
    remaining = timedelta(seconds=IMAGE_RATE_LIMIT_SECONDS) - elapsed
    return max(0, int(remaining.total_seconds()))


def record_image_request(user_id: int):
    last_image_request[user_id] = datetime.now(timezone.utc)


async def handle(ctx, args):
    if is_image_rate_limited(ctx.user_id):
        remaining = get_image_cooldown_remaining(ctx.user_id)
        minutes = remaining // 60
        seconds = remaining % 60
        await ctx.message.reply(f"Image cooldown. Try again in {minutes}m {seconds}s.")
    else:
        image_url = await gen_image(args.get("prompt", ctx.content))
        await ctx.message.reply(image_url)
        record_image_request(ctx.user_id)
    # Return None to signal early return (don't persist to session)
    return None
