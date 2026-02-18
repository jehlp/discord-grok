import asyncio

from .config import MODEL, IMAGE_MODEL
from .clients import xai


async def with_retry(func, *args, max_retries=3, **kwargs):
    """Run a function with exponential backoff retry on 503 errors."""
    for attempt in range(max_retries):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as e:
            if "503" in str(e) or "capacity" in str(e).lower():
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1  # 1s, 3s, 5s
                    await asyncio.sleep(wait_time)
                    continue
            raise
    raise Exception("Max retries exceeded")


def get_response_text(response) -> str:
    for item in response.output:
        if hasattr(item, "content"):
            for block in item.content:
                if hasattr(block, "text"):
                    return block.text
    return ""


async def query_with_search(messages: list[dict]) -> str:
    input_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
    response = await with_retry(
        xai.responses.create,
        model=MODEL,
        input=input_msgs,
        tools=[{"type": "web_search"}],
    )
    return get_response_text(response)


async def generate_image(prompt: str) -> str:
    response = await with_retry(
        xai.images.generate, model=IMAGE_MODEL, prompt=prompt
    )
    return response.data[0].url
