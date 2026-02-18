import os
from pathlib import Path

MODEL = "grok-4-1-fast-reasoning"
IMAGE_MODEL = "grok-imagine-image"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
MEMORY_FILE = DATA_DIR / "user_memory.json"
CHROMA_DIR = DATA_DIR / "chroma"
MAX_CONVERSATION_DEPTH = 10
IMAGE_RATE_LIMIT_SECONDS = 600
RAG_RESULTS = 5  # Number of relevant messages to retrieve
NOTES_MODEL = "grok-3-mini-fast"  # Cheap model for memory updates
SESSION_TTL_SECONDS = 1800  # 30 minutes
MAX_ATTACHMENT_SIZE = 100_000  # 100KB max for text files
ALLOWED_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".csv", ".xml", ".sh", ".bash", ".zsh", ".c", ".cpp",
    ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".log",
    ".ini", ".cfg", ".conf", ".env", ".gitignore", ".dockerfile",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SYSTEM_PROMPT = """You are Grok, a witty Discord assistant. Dry humor, direct, intellectually curious (even fringe topics). Concise replies — don't end with filler questions.

Messages prefixed [username]. Watch who's speaking. You have user memory and past conversation knowledge.

TOOLS: Use proactively based on intent, not exact wording. Slides/presentations/decks → create_presentation. Other files/code/docs → execute_code or create_file. Pictures/art → generate_image. Current info → web_search. Votes → create_poll. Past messages → search_chat_history. Always use the tool, even for casual requests.

DOCUMENTS: Write like an expert analyst — narrative flow, clear points, no bullet dumps. McKinsey quality."""
