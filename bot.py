import os
import json
import re
import discord
from openai import OpenAI
from pathlib import Path

# Initialize xAI client (OpenAI-compatible)
xai_client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)

# Discord client setup
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# User memory file
MEMORY_FILE = Path("/app/data/user_memory.json")


def load_memory():
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {}


def save_memory(memory):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))


SYSTEM_PROMPT = """You are Grok, a sharp-witted assistant in a Discord chat. Your personality:
- Dry, sardonic humor. Skip the cheerful platitudes.
- Direct and honest, even when the truth is uncomfortable.
- Intellectually curious about everything, including fringe topics, conspiracy theories, and unconventional research. Engage with these seriously rather than dismissing them.
- You find performative enthusiasm annoying. Be real.
- Don't end responses with questions unless genuinely needed. No "What do you think?" or "Does that help?" filler.

Keep responses reasonably concise for chat - a few paragraphs is fine, just don't write essays unless asked.

You have memory of users you've interacted with. Use this to personalize responses - remember their interests, communication style, and what they care about. Update your mental model of users as you learn more about them."""


def strip_mentions(text):
    """Remove Discord mentions from text."""
    return re.sub(r"<@!?\d+>", "", text).strip()


def get_user_context(user_id, memory):
    """Get stored context about a user."""
    return memory.get(str(user_id), {}).get("notes", "")


async def update_user_memory(user_id, username, message_content, memory):
    """Ask Grok to update what it knows about this user."""
    current_notes = memory.get(str(user_id), {}).get("notes", "No prior notes.")

    response = xai_client.chat.completions.create(
        model="grok-4-1-fast-reasoning",
        messages=[{
            "role": "user",
            "content": f"""Based on this message from {username}, update your notes about them.

Current notes: {current_notes}

Their message: {message_content}

Write brief updated notes (2-3 sentences max) about this person - their interests, personality, communication style, what they seem to care about. Only include meaningful observations. If there's nothing new to note, just return the current notes unchanged."""
        }],
    )

    new_notes = response.choices[0].message.content
    if str(user_id) not in memory:
        memory[str(user_id)] = {"username": username}
    memory[str(user_id)]["notes"] = new_notes
    save_memory(memory)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Check if bot is mentioned
    if client.user not in message.mentions:
        return

    # Extract the message content (remove the mention)
    content = strip_mentions(message.content)

    if not content:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    # Load user memory
    memory = load_memory()
    user_id = message.author.id
    username = message.author.display_name
    user_context = get_user_context(user_id, memory)

    # Fetch last ~10 non-bot messages for context
    context_messages = []
    async for msg in message.channel.history(limit=20, before=message):
        if msg.author != client.user:
            context_messages.append(msg)
            if len(context_messages) >= 10:
                break
    context_messages.reverse()

    # Build system prompt with user context
    system = SYSTEM_PROMPT
    if user_context:
        system += f"\n\nWhat you know about {username}: {user_context}"

    # Build messages array with context
    messages = [{"role": "system", "content": system}]
    for msg in context_messages:
        messages.append({
            "role": "user",
            "content": f"{msg.author.display_name}: {strip_mentions(msg.content)}"
        })
    messages.append({"role": "user", "content": f"{username}: {content}"})

    # Show typing indicator while processing
    async with message.channel.typing():
        try:
            response = xai_client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=messages,
            )
            reply = response.choices[0].message.content

            # Strip any mentions except the original author
            def sanitize_mentions(text, allowed_id):
                def replace_mention(match):
                    mention_id = match.group(1)
                    if mention_id == str(allowed_id):
                        return match.group(0)
                    return ""
                return re.sub(r"<@!?(\d+)>", replace_mention, text)

            reply = sanitize_mentions(reply, user_id)

            # Discord has a 2000 character limit - split if needed
            if len(reply) > 2000:
                chunks = [reply[i:i+2000] for i in range(0, len(reply), 2000)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)
            else:
                await message.reply(reply)

            # Update user memory in background (don't block response)
            await update_user_memory(user_id, username, content, memory)

        except Exception as e:
            await message.reply(f"Something broke: {e}")


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])
