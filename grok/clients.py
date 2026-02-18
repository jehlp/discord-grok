import os

import discord
import chromadb
from openai import OpenAI

from .config import CHROMA_DIR

xai = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
bot = discord.Client(intents=intents)

CHROMA_DIR.mkdir(parents=True, exist_ok=True)
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
message_collection = chroma_client.get_or_create_collection(
    name="server_messages",
    metadata={"hnsw:space": "cosine"}
)
