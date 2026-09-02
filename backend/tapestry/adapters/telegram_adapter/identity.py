"""Persona identity for the Telegram surface — name-prefixed text.

Telegram has no per-message username/avatar override for a bot posting into
a normal group (verified against python-telegram-bot 22.8.0 / Bot API 10.0
— see `docs/vendor-research/ANALYSIS-python-telegram-bot.md` §1: the full
`Bot.send_message` signature has no `username`/`display_name`/`avatar_url`
parameter anywhere, and the three fields that could plausibly fake one are
each disqualified for a different reason — `sender_chat` is a read-only
field on *incoming* messages, `business_connection_id` requires a real
human's Telegram Business account to have explicitly connected the bot
(not provisionable per-persona), and `author_signature` only applies to
channel posts with "Sign messages" enabled). So unlike the Discord adapter's
webhook username/avatar override, every persona's message on this surface
is distinguished the cheap way instead: a bold name prefix on its own line,
per that research doc's own ranked recommendation (option 2 — cheapest to
operate, weakest visual distinction, upgradeable later to one-bot-per-persona
without touching the approval or topic-routing code at all).

Judgment call: HTML `parse_mode`, not MarkdownV2
--------------------------------------------------
Telegram supports two real formatting modes here: MarkdownV2 and HTML
(legacy "Markdown" is deprecated by the library itself and not worth
starting with). MarkdownV2 requires escaping a long, *context-sensitive*
set of characters (`_ * [ ] ( ) ~ \\` > # + - = | { } . !`) everywhere they
appear outside intentional formatting spans — and a persona's free-form,
model-generated reply routinely contains a bare "." or "-" or "_"
(`config.py`, "step 1 - do X", `snake_case_name`) that would otherwise
send `Bot.send_message`/`edit_message_text` a malformed entity and get the
whole call rejected with a 400. There is no principled way to tell "this
underscore is prose" from "this underscore was meant to start italics"
without re-parsing the model's own output, and getting it wrong silently
breaks message delivery — not just cosmetics.

HTML mode has none of that ambiguity: exactly three characters (`&`, `<`,
`>`) need escaping, unconditionally, via the stdlib's `html.escape`, and
nothing else is special. That's what this module uses.
`TELEGRAM_PARSE_MODE` is exported so `bot.py` sends and edits every
persona-formatted string with the same mode consistently.
"""

from __future__ import annotations

from html import escape as _html_escape

from telegram.constants import ParseMode

from tapestry.core.personas import Persona

__all__ = ["TELEGRAM_PARSE_MODE", "format_persona_message"]

# Every call site in bot.py that sends or edits a `format_persona_message`
# result must pass this as `parse_mode` — see the module docstring for why
# HTML over MarkdownV2.
TELEGRAM_PARSE_MODE = ParseMode.HTML


def format_persona_message(persona: Persona, text: str) -> str:
    """Prefix `text` with `persona.name`, bolded, on its own line.

    Shape matches the task brief's own example
    (`f"*{persona.name}*\\n{text}"`) adapted to Telegram's supported parse
    modes — see the module docstring for why this uses HTML (`<b>...</b>`)
    rather than MarkdownV2 (`*...*`). Both `persona.name` and `text` are
    HTML-escaped; the returned string is only safe to send/edit with
    `parse_mode=TELEGRAM_PARSE_MODE`.
    """
    name = _html_escape(persona.name)
    body = _html_escape(text)
    return f"<b>{name}</b>\n{body}"
