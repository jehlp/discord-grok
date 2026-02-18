import json

from .config import MODEL, SYSTEM_PROMPT
from .clients import bot, xai
from .sessions import update_session, cleanup_sessions
from .memory import load_memory, get_user_notes, extract_mentioned_user_ids, find_referenced_users, update_user_notes
from .rag import store_message, retrieve_relevant_context
from .api import with_retry
from .context import build_context, get_ambient_context, is_reply_to_bot
from .helpers import strip_mentions, read_attachments, sanitize_reply, send_reply
from .tools import TOOLS, ToolContext, dispatch


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.loop.create_task(cleanup_sessions())


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    content = strip_mentions(message.content)

    # Store ALL messages for RAG (even from non-grok channels)
    if content:
        channel_name = getattr(message.channel, "name", "DM")
        store_message(
            message_id=str(message.id),
            content=content,
            author=message.author.display_name,
            channel=channel_name,
            timestamp=message.created_at.isoformat(),
        )

    # Only respond in grok channels
    channel_name = getattr(message.channel, "name", "").lower()
    if "grok" not in channel_name:
        return

    # Only respond to mentions or replies
    is_mention = bot.user in message.mentions
    is_reply = is_reply_to_bot(message)

    if not is_mention and not is_reply:
        return

    # Read any attached files and images
    attachments_content, image_urls = await read_attachments(message.attachments)

    if not content and not attachments_content and not image_urls:
        await message.reply("You pinged me for... nothing? Impressive.")
        return

    await handle_grok_message(message, content, attachments_content, image_urls)


async def handle_grok_message(message, content, attachments_content, image_urls):
    user_id = message.author.id
    username = message.author.display_name

    # Build conversation from reply chain or session
    conversation, thread_msg_ids = await build_context(message)

    # Append attachment content and images to the last user message
    if (attachments_content or image_urls) and conversation:
        for i in range(len(conversation) - 1, -1, -1):
            if conversation[i]["role"] == "user":
                if attachments_content:
                    attachment_text = "\n\n--- Attached Files ---"
                    for att in attachments_content:
                        attachment_text += f"\n\n### {att['filename']}\n```\n{att['content']}\n```"
                    conversation[i]["content"] += attachment_text

                if image_urls:
                    parts = [{"type": "text", "text": conversation[i]["content"]}]
                    for url in image_urls:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                    conversation[i]["content"] = parts
                break

    # Load user memory and find referenced users
    memory = load_memory()
    user_notes = get_user_notes(user_id, memory)
    mentioned_ids = extract_mentioned_user_ids(message.content)

    full_conversation_text = " ".join(
        m["content"] if isinstance(m["content"], str)
        else " ".join(p["text"] for p in m["content"] if p["type"] == "text")
        for m in conversation
    )
    referenced_users = find_referenced_users(full_conversation_text, memory, exclude_user_id=user_id, mentioned_ids=mentioned_ids)

    # Retrieve relevant past messages via RAG
    rag_context = retrieve_relevant_context(content, exclude_ids=thread_msg_ids)

    # Fetch ambient channel context
    ambient = await get_ambient_context(message.channel, user_id)

    # Build system prompt
    system = build_system_prompt(username, user_notes, referenced_users, rag_context, ambient)
    messages = [{"role": "system", "content": system}] + conversation

    # Query and handle response
    async with message.channel.typing():
        try:
            response = await with_retry(
                xai.chat.completions.create,
                model=MODEL,
                messages=messages,
                tools=TOOLS,
            )

            ctx = ToolContext(
                message=message,
                messages=messages,
                conversation=conversation,
                system=system,
                content=content,
                user_id=user_id,
                username=username,
                memory=memory,
            )

            reply = await handle_response(response, ctx)

            # Persist conversation to session (strip image parts -- URLs expire)
            if reply:
                session_msgs = []
                for msg in conversation:
                    if isinstance(msg["content"], list):
                        text = " ".join(p["text"] for p in msg["content"] if p["type"] == "text")
                        session_msgs.append({"role": msg["role"], "content": text})
                    else:
                        session_msgs.append(msg)
                session_msgs.append({"role": "assistant", "content": reply})
                update_session(user_id, session_msgs)

        except Exception as e:
            await message.reply(f"Something broke: {e}")


def build_system_prompt(username, user_notes, referenced_users, rag_context, ambient):
    system = SYSTEM_PROMPT

    if user_notes:
        system += f"\n\nWhat you know about {username}: {user_notes}"

    if referenced_users:
        system += "\n\nOther people mentioned that you know about:"
        for ref_name, ref_notes in referenced_users.items():
            system += f"\n- {ref_name}: {ref_notes}"

    if rag_context:
        system += "\n\nRelevant past conversations from this server:"
        for ctx in rag_context[:5]:
            system += f"\n- [{ctx['channel']}] {ctx['author']}: {ctx['content'][:200]}"

    if ambient:
        system += ambient

    return system


async def handle_response(response, ctx):
    choice = response.choices[0]

    # No tool calls -- straightforward text response
    if not choice.message.tool_calls:
        reply = choice.message.content
        reply = sanitize_reply(reply, ctx.user_id)
        await send_reply(ctx.message, reply)
        await update_user_notes(ctx.user_id, ctx.username, ctx.content, ctx.memory)
        return reply

    # Handle tool calls
    for tool_call in choice.message.tool_calls:
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        reply = await dispatch(name, ctx, args)
        return reply

    return None
