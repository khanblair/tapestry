"""Multi-persona co-presence in one Discord channel via one reusable webhook.

Per `docs/vendor-research/ANALYSIS-discordpy.md` (verified against real
source, `discord/webhook/async_.py:1711`): `Webhook.send()` takes
`username`/`avatar_url` as per-call keyword overrides of the webhook's own
stored identity -- they apply to that one message only. So exactly one
webhook, named `"tapestry"`, created once per channel (requires
`Permissions.manage_webhooks`) and reused indefinitely, is enough to make
every persona (Ada/Rex/Vex/Nova/...) show up in Discord under its own
name and avatar -- no per-persona webhook object, and no multi-bot-
application/multi-token architecture.

Judgment call: avatar URLs
---------------------------
`core.personas.Persona` has no avatar-URL field yet (`id`, `name`, `role`,
`model`, `system_prompt`, `tools`, `mcp_servers`, `status`, `color` --
see `core/personas.py`). Adding one would be a schema change to a module
outside this adapter's scope. Rather than leave every persona faceless,
`persona_avatar_url` generates a deterministic placeholder via DiceBear's
free, no-auth HTTP API (`https://api.dicebear.com`), seeded on the
persona's own name and colored with the persona's own `color` field --
same persona always renders the same avatar, no local asset pipeline, no
API key. Discord fetches and caches whatever URL `avatar_url` points to
itself; if `api.dicebear.com` is ever unreachable at send time, Discord
falls back to the webhook's own stored default avatar rather than the
send failing outright. This is a real external network dependency,
documented here rather than silently assumed -- a future iteration could
swap it for a locally generated/stored image with zero call-site changes,
since every caller only ever sees the returned URL string.
"""

from __future__ import annotations

import urllib.parse

import discord

from tapestry.core.personas import Persona

__all__ = [
    "WEBHOOK_NAME",
    "persona_avatar_url",
    "get_or_create_persona_webhook",
    "post_as_persona",
]

# One webhook, reused across every persona in the channel -- see module
# docstring. The name is how `get_or_create_persona_webhook` recognizes
# "our" webhook among any others a channel might have.
WEBHOOK_NAME = "tapestry"

_AVATAR_BASE_URL = "https://api.dicebear.com/9.x/initials/png"


def persona_avatar_url(persona: Persona) -> str:
    """Deterministic placeholder avatar URL for `persona`.

    Same persona (same `name` + `color`) always yields the same URL, so a
    persona's face stays stable across every message it ever posts.
    """
    seed = urllib.parse.quote(persona.name)
    background = urllib.parse.quote(persona.color.lstrip("#"))
    return f"{_AVATAR_BASE_URL}?seed={seed}&backgroundColor={background}"


async def get_or_create_persona_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """Return the channel's `"tapestry"` webhook, creating it if absent.

    Requires `Permissions.manage_webhooks` on the bot's member in this
    channel (verified: `TextChannel.create_webhook`,
    `discord/channel.py:749`) -- a `discord.Forbidden` from either
    `channel.webhooks()` or `channel.create_webhook()` is allowed to
    propagate rather than being swallowed here, since a caller needs to
    know permissions are missing, not silently get no webhook back.
    """
    existing = await channel.webhooks()
    for webhook in existing:
        if webhook.name == WEBHOOK_NAME:
            return webhook
    return await channel.create_webhook(name=WEBHOOK_NAME)


async def post_as_persona(
    channel: discord.TextChannel,
    persona: Persona,
    content: str,
    **kwargs: object,
) -> discord.WebhookMessage:
    """Post `content` in `channel`, appearing as `persona`.

    `username`/`avatar_url` are always set from `persona`; `wait=True` is
    the default (overridable via `kwargs`) so the real posted
    `WebhookMessage` comes back rather than `None` -- callers that want to
    edit this message later (e.g. to append a follow-up) or spin off a
    thread from it need the real message object. `kwargs` is forwarded
    verbatim to `Webhook.send` (e.g. `thread=` to post into a spun-off
    Discord thread rather than the parent channel, `embed=`, `view=`,
    ...), and can override any of the defaults set here.
    """
    webhook = await get_or_create_persona_webhook(channel)
    send_kwargs: dict[str, object] = {
        "content": content,
        "username": persona.name,
        "avatar_url": persona_avatar_url(persona),
        "wait": True,
    }
    send_kwargs.update(kwargs)
    message = await webhook.send(**send_kwargs)
    assert message is not None, "wait=True guarantees Webhook.send returns a WebhookMessage"
    return message
