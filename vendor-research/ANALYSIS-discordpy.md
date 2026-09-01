# discord.py — Analysis

Repo: `vendor-research/discordpy` (shallow clone, commit `65232c3`, `discord/__init__.py` reports version `2.8.0a`)

## Summary

The core claim holds: **confirmed, not busted.** `Webhook.send()` genuinely takes `username`/`avatar_url` as per-call kwargs that override the webhook's stored identity for that one message only. One webhook, created once per channel, is reusable indefinitely across as many personas as needed — no per-identity webhook object and no multi-bot-application/multi-token architecture required. This does not change the Discord-first build-sequence recommendation.

## 1. Webhook send signature

`discord/webhook/async_.py:1711`:

```python
async def send(
    self, content: str = MISSING, *,
    username: str = MISSING, avatar_url: Any = MISSING,
    tts: bool = False, ephemeral: bool = False,
    file: File = MISSING, files: Sequence[File] = MISSING,
    embed: Embed = MISSING, embeds: Sequence[Embed] = MISSING,
    allowed_mentions: AllowedMentions = MISSING, view: BaseView = MISSING,
    thread: Snowflake = MISSING, thread_name: str = MISSING,
    wait: bool = False, suppress_embeds: bool = False, silent: bool = False,
    applied_tags: List[ForumTag] = MISSING, poll: Poll = MISSING,
) -> Optional[WebhookMessage]:
```

Docstring: *"username: ... If no username is provided then the default username for the webhook is used."* / *"avatar_url: ... If no avatar URL is provided then the default avatar for the webhook is used."*

## 2. Permissions and reuse

`Permissions.manage_webhooks` (`discord/permissions.py:691`) is required for `create_webhook(name, avatar=None, reason=None)`, defined identically on `TextChannel`/`VoiceChannel`/`ForumChannel` (`discord/channel.py:749,1394,3082`).

`Webhook.partial()` and `Webhook.from_url()` (`:1209`, `:1273`) rehydrate one stored webhook handle, which can then call `.send()` repeatedly with different `username`/`avatar_url` per call — one webhook object, N personas, no re-creation needed per persona.

## 3. Threads

`Message.create_thread(*, name, auto_archive_duration=MISSING, slowmode_delay=None, reason=None) -> Thread` (`discord/message.py:1639`), requires `create_public_threads`. Webhooks can also post directly into a thread via `send(..., thread=... / thread_name=...)`.

## 4. Interactive components (approve/reject)

`examples/views/confirm.py` is a working Approve/Reject-shaped example. Decorator at `discord/ui/button.py:316`:

```python
button(*, label=None, custom_id=None, disabled=False, style=ButtonStyle.secondary, emoji=None, row=None, id=None)
```

Callback signature is fixed as `(self, interaction: discord.Interaction, button: discord.ui.Button)`. For approvals that must survive a bot restart, use persistent views (`examples/views/persistent.py`) — a stable `custom_id` plus `bot.add_view()` at startup.

## 5. Async model / integration with our own loop

`Client.run()` (`discord/client.py:853`) wraps `asyncio.run(runner())` internally and is documented: *"must be the last function to call... If you want more control over the event loop, use `start()` coroutine instead."*

Recommended integration: `await bot.start(token)` inside our own `asyncio.run(main())`, with the LangGraph-orchestrated agent loop running as a sibling task on the same loop. If isolation is preferred instead, discord.py's own documented cross-thread pattern is `asyncio.run_coroutine_threadsafe(coro, client.loop)` (`docs/faq.rst`, ~line 240).

## 6. License and maturity

`LICENSE` file confirmed MIT (verbatim first line: "The MIT License (MIT)"). Actively maintained — frequent recent commits on `master`, currently pre-2.8.

## Recommendation

Confirmed pattern for our persona-message-posting layer:

```python
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def get_or_create_persona_webhook(channel: discord.TextChannel) -> discord.Webhook:
    hooks = await channel.webhooks()
    for h in hooks:
        if h.name == "tapestry":
            return h
    return await channel.create_webhook(name="tapestry")

async def post_as_persona(channel, persona_name: str, avatar_url: str, content: str):
    webhook = await get_or_create_persona_webhook(channel)
    await webhook.send(content=content, username=persona_name, avatar_url=avatar_url)

class ApproveReject(discord.ui.View):
    def __init__(self, on_decision):
        super().__init__(timeout=None)
        self.on_decision = on_decision

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="tapestry:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_decision(interaction, approved=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="tapestry:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_decision(interaction, approved=False)

async def main():
    async with bot:
        bot.add_view(ApproveReject(on_decision=handle_approval))  # persistent across restarts
        await bot.start(DISCORD_TOKEN)
        # run alongside the LangGraph agent loop as a sibling asyncio task
```

One webhook per channel, `username`/`avatar_url` swapped per call for each of Ada/Rex/Vex/Nova — exactly the mechanism the build-sequence recommendation assumed, now verified against the library's real signatures rather than general knowledge.
