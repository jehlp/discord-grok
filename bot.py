import os
import discord
from openai import OpenAI

# Initialize xAI client (OpenAI-compatible)
xai_client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)

# Discord client setup
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


SYSTEM_PROMPT = """You are Grok, a helpful assistant in a Discord chat. Keep responses concise and to the point - aim for 1-3 short paragraphs max. Avoid lengthy essays or excessive detail unless specifically asked for more depth."""


def strip_mentions(text):
    """Remove Discord mentions from text."""
    import re
    return re.sub(r"<@!?\d+>", "", text).strip()


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
        await message.reply("Please include a message after mentioning me.")
        return

    # Fetch last ~10 non-bot messages for context
    context_messages = []
    async for msg in message.channel.history(limit=20, before=message):
        if msg.author != client.user:
            context_messages.append(msg)
            if len(context_messages) >= 10:
                break
    context_messages.reverse()

    # Build messages array with context
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in context_messages:
        messages.append({
            "role": "user",
            "content": f"{msg.author.display_name}: {strip_mentions(msg.content)}"
        })
    messages.append({"role": "user", "content": f"{message.author.display_name}: {content}"})

    # Show typing indicator while processing
    async with message.channel.typing():
        try:
            response = xai_client.chat.completions.create(
                model="grok-3-mini",
                messages=messages,
            )
            reply = response.choices[0].message.content

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

        except Exception as e:
            await message.reply(f"Error: {e}")


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])
