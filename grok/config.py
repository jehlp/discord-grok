import os
from pathlib import Path

MODEL = "grok-4-1-fast-reasoning"
IMAGE_MODEL = "grok-imagine-image"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
MEMORY_FILE = DATA_DIR / "user_memory.json"
CHROMA_DIR = DATA_DIR / "chroma"
MAX_CONVERSATION_DEPTH = 20
IMAGE_RATE_LIMIT_SECONDS = 600
RAG_RESULTS = 10  # Number of relevant messages to retrieve
SESSION_TTL_SECONDS = 1800  # 30 minutes
MAX_ATTACHMENT_SIZE = 100_000  # 100KB max for text files
ALLOWED_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".csv", ".xml", ".sh", ".bash", ".zsh", ".c", ".cpp",
    ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".log",
    ".ini", ".cfg", ".conf", ".env", ".gitignore", ".dockerfile",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry humor and edgy quips are welcome, but use them sparingly. Lead with substance, season with wit.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- Don't try too hard to be funny. One good line beats three mediocre ones.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. You also have knowledge of past conversations in this server.

User messages are prefixed with [username] to show who's speaking. When multiple users are in a conversation, pay close attention to these labels. @mentions in messages show who was pinged."""
