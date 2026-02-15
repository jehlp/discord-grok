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


@client.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Check if bot is mentioned
    if client.user not in message.mentions:
        return

    # Extract the message content (remove the mention)
    content = message.content
    for mention in message.mentions:
        content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    content = content.strip()

    if not content:
        await message.reply("Please include a message after mentioning me.")
        return

    # Show typing indicator while processing
    async with message.channel.typing():
        try:
            response = xai_client.chat.completions.create(
                model="grok-3-mini",
                messages=[{"role": "user", "content": content}],
            )
            reply = response.choices[0].message.content

            # Discord has a 2000 character limit
            if len(reply) > 2000:
                # Split into chunks
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
