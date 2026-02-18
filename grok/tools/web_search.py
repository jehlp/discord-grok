from ..api import query_with_search

DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web. Use broadly — any question about current events, news, prices, weather, scores, facts you're unsure about, or anything that benefits from real-time info. When in doubt, search.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
            },
            "required": ["query"],
        },
    },
}


async def handle(ctx, args):
    result = await query_with_search(ctx.messages)
    return result or "No results found."
