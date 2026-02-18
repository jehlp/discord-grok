import os

from .clients import bot
from . import handler  # noqa: F401 — registers @bot.event handlers

bot.run(os.environ["DISCORD_TOKEN"])
