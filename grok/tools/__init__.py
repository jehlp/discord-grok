import importlib
import pkgutil
from dataclasses import dataclass, field

import discord


@dataclass
class ToolContext:
    message: discord.Message
    messages: list[dict]       # full prompt (system + conversation)
    conversation: list[dict]   # just conversation part
    system: str
    content: str               # stripped user message
    user_id: int
    username: str
    memory: dict
    replied: bool = field(default=False)  # set True when tool sends a Discord message


# Auto-discover tool modules and collect DEFINITION/handle pairs
TOOLS: list[dict] = []
HANDLERS: dict[str, callable] = {}

for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    _mod = importlib.import_module(f".{_name}", __package__)
    if hasattr(_mod, "DEFINITION") and hasattr(_mod, "handle"):
        TOOLS.append(_mod.DEFINITION)
        func_name = _mod.DEFINITION["function"]["name"]
        HANDLERS[func_name] = _mod.handle


async def dispatch(name: str, ctx: ToolContext, args: dict) -> str | None:
    """Dispatch a tool call by name. Returns reply text or None."""
    handler = HANDLERS.get(name)
    if handler:
        return await handler(ctx, args)
    return None
