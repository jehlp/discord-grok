# Grok

A Discord bot that actually does things. Powered by xAI's Grok model.

---

## How to talk to it

Grok only hangs out in channels with **"grok" in the name**. To get its attention:

- **@mention it** — `@grok what's the weather in Phoenix?`
- **Reply to one of its messages** — it'll pick up the thread and remember what you were talking about

---

## What it can do

### Just... talk to it
Ask it anything. It has a dry sense of humor, gives direct answers, and won't pad responses with filler. It remembers things about you over time — your interests, your vibe, recurring topics — so conversations get better the more you use it.

### Search the web
It searches the web automatically whenever a question needs current information. News, scores, prices, recent events, anything that might have changed since it was trained — it'll look it up without you having to ask.

### Generate images
Ask it to draw, render, visualize, or make a picture of anything.

> *"draw me a frog in a business suit"*
> *"make a meme about Mondays"*
> *"show me what a medieval McDonald's would look like"*

Images have a **10-minute cooldown** per person.

### Build a PowerPoint
Ask for slides, a deck, or a presentation on any topic and it'll create a fully formatted `.pptx` file and upload it directly to Discord.

> *"make a presentation about why cats are better than dogs"*
> *"generate slides on the history of the Roman Empire"*

It'll research the topic online if needed, write actual prose (not lazy bullet fragments), and deliver a real deck. **10-minute cooldown** per person.

### Create files
Ask it to write a script, a config file, a markdown doc, code in any language, or a spreadsheet/Word doc. It'll generate the file and upload it to the channel.

> *"write me a Python script that renames all my files"*
> *"make a .docx resume template"*
> *"create a YAML config for an nginx server"*

### Run a poll
Ask it to put something to a vote.

> *"poll: should we do pizza or tacos for Friday?"*
> *"let's vote on the best Star Wars movie"*

The poll posts as a native Discord poll with a 24-hour timer by default (you can ask for a different duration).

### Pin a message
It can pin messages it deems truly exceptional, funny, or legendary. It won't do this often — only when something is genuinely worth preserving for posterity.

### Search chat history
Ask it to dig through the channel's past messages.

> *"find the funniest thing anyone said this week"*
> *"who was talking about crypto yesterday?"*
> *"scroll back and find that link about the drone video"*

It can look back up to 30 days.

---

## Memory

Grok builds a mental model of each person it talks to over time. It doesn't forget. If you told it three weeks ago that you hate cilantro or that you're a CS major, it might bring that up when it's relevant. There's no way to opt out of this; it's just how it works.

---

## Limits

| Feature | Cooldown |
|---|---|
| Image generation | 10 minutes per person |
| Presentations | 10 minutes per person |
| Code / file execution | 10 minutes per person |

Everything else (search, polls, chat, history) has no cooldown.
