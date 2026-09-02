"""The Telegram surface — the SECOND chat adapter, per the build sequence in
`tapestry_scoped_spec.md` ("no avatar override forces the core abstraction
to handle a more constrained surface honestly").

Structured to mirror `adapters/discord_adapter/bot.py` wherever the two
platforms' primitives actually line up (`ensure_conversation_row`/
`ensure_conversation`'s two-layer split, `render_stream_frame`,
`last_assistant_text`, `drive_graph` using `astream(stream_mode="custom")`
for live status then `aget_state(config)` once for the final
values/interrupts, the DM-or-addressed gate before a default persona ever
answers) — see each judgment-call note below for where and why Telegram's
constraints force a real difference instead.

Required one-time setup, not something this code can do for you
-------------------------------------------------------------------
**`/setprivacy` must be disabled for this bot via @BotFather**
(BotFather -> `/mybots` -> select the bot -> Bot Settings -> Group Privacy
-> Turn off), or Telegram will never deliver ordinary group messages to
this bot at all — only messages that are commands or that explicitly
`@mention` the bot's own Telegram username. This is a one-time,
human-operated configuration step against Telegram's own servers; nothing
in this module can inspect or change it, so it's documented here as the
single most common reason "the bot is running but never receives group
messages."

Naming collision to keep straight
------------------------------------
LangGraph's `config["configurable"]["thread_id"]` (its own checkpoint/
session identity) is mapped to OUR `conversation_id` (`f"telegram-{chat.id}"`
below) — NOT to Telegram's own `message_thread_id` (a forum-topic id) and
NOT to `TapestryGraphState["thread_id"]` (our UI reply-thread field, see
`graph/build.py`). All three are different things that happen to share the
word "thread":
  - `config["configurable"]["thread_id"]`  -> our `conversation_id`
  - Telegram `Message.message_thread_id`   -> our `TapestryGraphState["thread_id"]`
    (only when the chat is a forum supergroup; see "Forum topics" below)
  - Tapestry's own UI-thread feature       -> `TapestryGraphState["thread_id"]`
    (same field as the line above -- the forum topic IS this surface's
    closest real analog to that UI feature, per the task brief)

Update-delivery mechanism (judgment call)
--------------------------------------------
`docs/vendor-research/ANALYSIS-python-telegram-bot.md` §4 confirms both
long-polling and webhooks are fully supported, and that the library's own
docstring on `Application.run_polling`/`run_webhook` explicitly warns
against calling either from a process that already owns its asyncio event
loop (they block until a stop signal, which would starve every other
sibling task -- Discord's adapter, the web adapter's uvicorn server, this
process's own LangGraph work). This module never calls either convenience
wrapper. Also never `Application.run_polling`'s cousin at the OTHER
extreme -- manually feeding `application.update_queue` from an HTTP route
(the library's own `examples/customwebhookbot/starlettebot.py` pattern) --
since that needs a public HTTPS endpoint `web_adapter` doesn't expose yet
(it isn't built). Instead, **manual-lifecycle polling**:
    await application.initialize()
    await application.start()
    await application.updater.start_polling(...)
`Updater.start_polling()` is not the blocking `run_polling()` wrapper: per
its own signature/docstring it starts a background fetch loop and returns
promptly. So `start()` below returns quickly without blocking the shared
event loop. Webhook mode via `Application.builder().updater(None)` + a
FastAPI route pushing into `update_queue` remains a valid upgrade path
later, entirely localized to `build_application`/`start` below, once
`web_adapter` exists and a public endpoint is available.

Forum topics (thread panel analog)
--------------------------------------
Per the verified research (§2), forum/topics support is real:
`Chat.is_forum`, `Message.message_thread_id`, and every `send_*`/
`edit_message_*` call's `message_thread_id` parameter. `forum_thread_id`
below maps Telegram's `message_thread_id` to `TapestryGraphState["thread_id"]`
**only when `chat.is_forum` is true** -- a plain group or DM has no
topics, so every message in it carries `thread_id=None` and gets no
thread separation at all (matching the task's documented fallback). All
topics within one forum supergroup still share the SAME `conversation_id`
(`f"telegram-{chat.id}"`, keyed on the chat, not the topic) and therefore
the SAME LangGraph checkpoint thread -- topics are a *display*/history
grouping (via `thread_id` on logged events, filtered by
`core.conversations.derive_messages`), not a separate graph execution
lane. One consequence worth naming explicitly: the pending-approval guard
in `on_message` (`snapshot.interrupts`, via `graph.aget_state`) is scoped
per-conversation (per chat), not per-topic, so an approval raised from one
topic blocks new turns from every topic in that same forum until it's
answered.

Judgment call: gate group replies on DM-or-addressed, like Discord
-----------------------------------------------------------------------
With `/setprivacy` off (required, see above), this bot receives EVERY
group message, not just ones meant for it. Discord's adapter gates on
`is_dm or self.user in message.mentions` before doing anything else, and
only resolves WHICH persona answers (name-match-or-default) once inside
that gate. Telegram has no equivalent boolean the way `discord.Message.
mentions` gives one directly, so `is_addressed()` below reconstructs the
same two-part check: a message is addressed either by naming a real
persona (`match_named_persona` -- the message's first word, stripped of
surrounding punctuation, matched case-insensitively against a persona's
`name`, EXACTLY the same rule as Discord's `resolve_persona_id`) or by
literally containing `@<bot_username>`. A DM always counts as addressed
(matching Discord's `is_dm` half of the same check). An unaddressed group
message is ignored entirely -- no event logged, no model call made --
which is what keeps `/setprivacy`-off from meaning "one model call per
line of human chit-chat." `DEFAULT_PERSONA_ID` only ever answers a message
that already passed this gate and didn't name anyone specific (a DM that
opens with "hey, can you look at this" rather than "Rex, can you...") --
same role Ada plays for Discord, for the same reason (`ada.yaml`: only
read-only tools, whole system prompt is triage/routing/delegation).

Judgment call: `aget_state(config).interrupts` as the pending-approval
source of truth, not a separate in-process routing table
-----------------------------------------------------------------------
An earlier draft of this module kept its own `dict[request_id ->
(chat_id, message_id, ...)]` to route an inline-keyboard click back to the
right conversation, because Telegram's `callback_data` is capped at 64
bytes and can't safely carry both a `conversation_id` and a `request_id`
(unlike Discord's 100-char `custom_id`, which embeds `conversation_id`
directly). That table doesn't survive a process restart, unlike the
checkpointed graph state itself -- a real gap. It turned out to be
unnecessary: `CallbackQuery.message` (like Discord's `interaction.
message`) already gives direct access to the clicked message's `chat`,
so `conversation_id` is always recoverable via `conversation_id_for_chat
(query.message.chat.id)` with no lookup table at all. `callback_data`
only needs to carry `request_id`, and `graph.aget_state(config).
interrupts` -- the checkpointer's own durable state, exactly what
`TapestryDiscordClient.handle_approval_click` already checks against --
tells us whether that `request_id` is still the live pending one. This is
strictly better than the table it replaced: it survives a restart, and it
can never desync from what the graph actually thinks is paused.

Judgment call: budget-exception guard kept, even though Discord's doesn't
have one yet
-----------------------------------------------------------------------
`graph.budgets.check_turn_budget`/`check_delegation_depth` raise by
design (`TurnBudgetExceeded`/`DelegationDepthExceeded`), and `core.
delegation.delegate()` raises `DelegationRoundLimitExceeded`. Left
uncaught, any of the three would escape `drive_graph` mid-`astream` and
leave the human staring at "is thinking…" forever, with no visible error.
`drive_graph` below catches all three (and, defensively, any other
unexpected exception) and edits the placeholder to a short failure
message instead. This is a genuine robustness addition over the current
Discord adapter, not a stylistic difference -- worth a look there too, but
out of this module's scope to fix.
"""

from __future__ import annotations

import contextlib
import json
import logging
import string
from asyncio import Lock
from datetime import datetime, timezone

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tapestry.adapters.telegram_adapter.identity import (
    TELEGRAM_PARSE_MODE,
    format_persona_message,
)
from tapestry.core import events
from tapestry.core.conversations import derive_messages
from tapestry.core.personas import Persona
from tapestry.graph.budgets import (
    DelegationDepthExceeded,
    DelegationRoundLimitExceeded,
    TurnBudgetExceeded,
)
from tapestry.graph.build import PERSONAS, build_graph, new_state
from tapestry.storage.db import get_connection

__all__ = [
    "DEFAULT_PERSONA_ID",
    "conversation_id_for_chat",
    "conversation_kind",
    "forum_thread_id",
    "match_named_persona",
    "is_addressed",
    "render_stream_frame",
    "last_assistant_text",
    "decode_callback_data",
    "ensure_conversation_row",
    "TelegramAdapter",
    "start",
    "stop",
]

logger = logging.getLogger(__name__)

# See "Judgment call: gate group replies on DM-or-addressed" above -- same
# persona, same reasoning as Discord's adapter (`ada.yaml`: read-only
# tools only, whole system prompt is triage/routing/delegation).
DEFAULT_PERSONA_ID = "ada"

_APPROVE = "approve"
_REJECT = "reject"


# ---------------------------------------------------------------------------
# Pure helpers — conversation/thread/persona resolution, callback_data
# encode/decode, stream-frame rendering. Deliberately free functions (not
# methods), mirroring discord_adapter/bot.py, so they're testable without
# constructing a real `Application`/`Bot`.
# ---------------------------------------------------------------------------


def conversation_id_for_chat(chat_id: int) -> str:
    return f"telegram-{chat_id}"


def conversation_kind(chat_type: str) -> str:
    """Matches `storage/schema.sql`'s `CHECK (kind IN ('dm', 'group'))`."""
    return "dm" if chat_type == Chat.PRIVATE else "group"


def forum_thread_id(is_forum: bool, message_thread_id: int | None) -> int | None:
    """Telegram `message_thread_id` -> our `thread_id`, forum chats only.

    See the module docstring's "Forum topics" section -- a non-forum chat
    always resolves to `None`, never leaking a stray `message_thread_id`
    (Telegram sets one for "General" replies in some clients even without
    Topics enabled) into thread-scoped history filtering.
    """
    return message_thread_id if is_forum else None


def match_named_persona(personas: dict[str, Persona], text: str) -> Persona | None:
    """The message's first word, stripped of surrounding punctuation,
    matched case-insensitively against a persona's `name`. Identical rule
    to `discord_adapter.bot.resolve_persona_id` -- see that module and
    this one's "Judgment call: gate group replies" note.
    """
    stripped = text.strip()
    if not stripped:
        return None
    first_word = stripped.split(maxsplit=1)[0].strip(string.punctuation)
    if not first_word:
        return None
    for persona in personas.values():
        if persona.name.lower() == first_word.lower():
            return persona
    return None


def is_addressed(personas: dict[str, Persona], text: str, bot_username: str | None) -> bool:
    """Whether an (already-known-non-DM) group message is addressed to
    this bot at all -- names a real persona, or literally mentions the
    bot's own `@username`. See the module docstring's judgment-call note.
    """
    if match_named_persona(personas, text) is not None:
        return True
    if bot_username:
        return f"@{bot_username}".lower() in text.lower()
    return False


def render_stream_frame(personas: dict[str, Persona], frame: dict) -> str | None:
    """Turn one `graph.streaming.emit(...)` frame into status text, or
    `None` for a frame that shouldn't cause an edit (`persona/responded`
    carries no new information the human needs mid-flight -- the actual
    reply follows as its own message once the run finishes).
    """
    frame_type = frame.get("type")
    payload = frame.get("payload") or {}

    if frame_type == "persona/thinking":
        persona = personas.get(payload.get("persona_id", ""))
        name = persona.name if persona else payload.get("persona_id", "…")
        return f"\U0001f4ad {name} is thinking…"

    if frame_type == "tool/status":
        tool_name = payload.get("tool_name", "tool")
        status = payload.get("status")
        if status == "running":
            return f"\U0001f527 running {tool_name}…"
        if status == "done":
            is_error = payload.get("is_error")
            icon = "⚠️" if is_error else "✅"
            outcome = "failed" if is_error else "done"
            return f"{icon} {tool_name} {outcome}"

    return None


def last_assistant_text(messages: list[dict]) -> str:
    """The most recent assistant-authored text in a graph state's
    `messages` list -- what actually gets posted as the persona's final
    reply. Empty string if there isn't one (defensive; shouldn't happen
    for a turn that reached `next_node == "end"`). Identical to
    `discord_adapter.bot.last_assistant_text`.
    """
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            if content:
                return content
    return ""


def decode_callback_data(data: str) -> tuple[str, str] | None:
    """`"approve:<request_id>"` / `"reject:<request_id>"` -> `(action,
    request_id)`, or `None` if `data` isn't ours. `request_id` alone (not
    `conversation_id:request_id` the way Discord's `custom_id` does it) --
    see the module docstring's judgment-call note on why that's safe here.
    """
    action, sep, request_id = data.partition(":")
    if not sep or action not in (_APPROVE, _REJECT) or not request_id:
        return None
    return action, request_id


def ensure_conversation_row(conversation_id: str, kind: str, name: str | None = None) -> None:
    """Create `conversations`' row for `conversation_id` if it doesn't
    already exist. Schema per `storage/schema.sql`:
    `conversations(id, kind, name, created_at)`, `kind IN ('dm', 'group')`.
    Identical to `discord_adapter.bot.ensure_conversation_row`.
    """
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO conversations (id, kind, name, created_at) VALUES (?, ?, ?, ?)",
        (
            conversation_id,
            kind,
            name,
            datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class TelegramAdapter:
    """Owns the running `Application`'s handlers and the small amount of
    in-process state that isn't itself durable via the event log /
    checkpointer: which conversations this process has already run
    startup repair on (`_known_conversations`, deliberately not
    persisted -- a fresh process should always re-check every conversation
    it touches for real, matching `TapestryDiscordClient`'s identical
    field), and a per-conversation lock so two updates for the same
    conversation can never drive the graph concurrently.
    """

    def __init__(
        self,
        graph: CompiledStateGraph,
        personas: dict[str, Persona] | None = None,
        default_persona_id: str = DEFAULT_PERSONA_ID,
    ) -> None:
        self.graph = graph
        self.personas = personas if personas is not None else PERSONAS
        self.default_persona_id = default_persona_id
        self._known_conversations: set[str] = set()
        self._conversation_locks: dict[str, Lock] = {}

    def _get_lock(self, conversation_id: str) -> Lock:
        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = Lock()
            self._conversation_locks[conversation_id] = lock
        return lock

    def _default_persona(self) -> Persona:
        default = self.personas.get(self.default_persona_id)
        if default is None and self.personas:
            default = next(iter(self.personas.values()))
        if default is None:
            raise RuntimeError("No personas are loaded; cannot resolve a default persona.")
        return default

    async def ensure_conversation(
        self, conversation_id: str, *, kind: str, name: str | None = None
    ) -> None:
        """Create the conversation row if absent; call
        `close_orphaned_turns` once per conversation per process lifetime.
        Identical two-layer split to `TapestryDiscordClient.
        ensure_conversation` / `ensure_conversation_row`.
        """
        if conversation_id in self._known_conversations:
            return
        ensure_conversation_row(conversation_id, kind, name)
        events.close_orphaned_turns(conversation_id)
        self._known_conversations.add(conversation_id)

    # -- driving the graph --------------------------------------------------------

    async def drive_graph(
        self,
        bot,
        chat_id: int,
        thread_id: int | None,
        conversation_id: str,
        graph_input: dict | Command,
        placeholder: Message,
    ) -> None:
        """Run (or resume) one graph step to completion or the next pause,
        forwarding `graph.streaming.emit(...)` frames onto `placeholder`
        as message edits, then either posting a new approval prompt
        (paused) or the persona's final reply via `identity.
        format_persona_message` + `bot.send_message` (finished). Mirrors
        `TapestryDiscordClient.drive_graph`'s structure -- `astream(...,
        stream_mode="custom")` for live status, then one `aget_state`
        read for the authoritative final values/interrupts, rather than
        re-deriving them from the stream itself.
        """
        config = {"configurable": {"thread_id": conversation_id}}

        try:
            async for frame in self.graph.astream(graph_input, config=config, stream_mode="custom"):
                await self._apply_stream_frame(placeholder, frame)
        except (TurnBudgetExceeded, DelegationDepthExceeded, DelegationRoundLimitExceeded) as exc:
            with contextlib.suppress(TelegramError):
                await placeholder.edit_text(text=f"⚠️ Stopped: {exc}")
            return
        except Exception:
            logger.exception("Unhandled error while running the Tapestry graph")
            with contextlib.suppress(TelegramError):
                await placeholder.edit_text(text="⚠️ Something went wrong running this turn.")
            return

        snapshot = await self.graph.aget_state(config)
        persona_id = snapshot.values.get("persona_id", self.default_persona_id)
        persona = self.personas.get(persona_id)

        if snapshot.interrupts:
            await self._post_approval_request(
                bot, chat_id, thread_id, persona, snapshot.interrupts[0], placeholder
            )
            return

        final_text = last_assistant_text(snapshot.values.get("messages", [])) or "(no response)"
        with contextlib.suppress(TelegramError):
            await placeholder.delete()

        if persona is None:
            await bot.send_message(chat_id=chat_id, text=final_text, message_thread_id=thread_id)
            return
        await bot.send_message(
            chat_id=chat_id,
            text=format_persona_message(persona, final_text),
            parse_mode=TELEGRAM_PARSE_MODE,
            message_thread_id=thread_id,
        )

    async def _apply_stream_frame(self, placeholder: Message, frame: dict) -> None:
        text = render_stream_frame(self.personas, frame)
        if text is None:
            return
        try:
            await placeholder.edit_text(text=text)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("Failed to edit Telegram status message: %s", exc)
        except TelegramError:
            logger.warning("Failed to edit Telegram status message", exc_info=True)

    async def _post_approval_request(
        self,
        bot,
        chat_id: int,
        thread_id: int | None,
        persona: Persona | None,
        interrupt_obj: object,
        placeholder: Message,
    ) -> None:
        value = interrupt_obj.value or {}
        request_id = value.get("request_id", "unknown")
        tool_name = value.get("tool_name")
        arguments = value.get("arguments") or {}
        persona_label = persona.name if persona else "A persona"
        detail = json.dumps(arguments, sort_keys=True)[:1500]
        body = f"wants to run {tool_name!r} — approve?\n{detail}"

        with contextlib.suppress(TelegramError):
            await placeholder.delete()

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"{_APPROVE}:{request_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"{_REJECT}:{request_id}"),
                ]
            ]
        )
        if persona is None:
            await bot.send_message(
                chat_id=chat_id,
                text=f"{persona_label} {body}",
                reply_markup=keyboard,
                message_thread_id=thread_id,
            )
            return
        await bot.send_message(
            chat_id=chat_id,
            text=format_persona_message(persona, body),
            parse_mode=TELEGRAM_PARSE_MODE,
            reply_markup=keyboard,
            message_thread_id=thread_id,
        )

    # -- Telegram handlers --------------------------------------------------------

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or not message.text:
            return
        chat = update.effective_chat
        if chat is None:
            return

        kind = conversation_kind(chat.type)
        is_dm = kind == "dm"
        if not is_dm and not is_addressed(self.personas, message.text, context.bot.username):
            return  # ungated group chatter, not addressed to any persona

        conversation_id = conversation_id_for_chat(chat.id)
        display_name = chat.title or (
            update.effective_user.full_name if update.effective_user else None
        )
        await self.ensure_conversation(conversation_id, kind=kind, name=display_name)

        thread_id = forum_thread_id(getattr(chat, "is_forum", False), message.message_thread_id)
        config = {"configurable": {"thread_id": conversation_id}}

        async with self._get_lock(conversation_id):
            snapshot = await self.graph.aget_state(config)
            if snapshot.interrupts:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=(
                        "There's an approval pending on this conversation — please "
                        "Approve or Reject it before sending a new message."
                    ),
                    message_thread_id=thread_id,
                )
                return

            persona = match_named_persona(self.personas, message.text) or self._default_persona()

            # core.conversations.Message.thread_id / TapestryGraphState
            # ["thread_id"] are both typed `str | None` (see
            # core/conversations.py and graph/build.py) -- Telegram's own
            # `message_thread_id` is an int, needed as-is for every
            # Telegram API call (`message_thread_id=thread_id` below and
            # throughout `drive_graph`), but must be stringified at this
            # boundary before it reaches core/graph state or the event log.
            core_thread_id = str(thread_id) if thread_id is not None else None

            # actor="human" per the task brief; the ONLY place a Telegram
            # inbound message becomes a durable core event.
            events.append_event(
                conversation_id,
                "user/message",
                actor="human",
                payload={
                    "text": message.text,
                    **({"thread_id": core_thread_id} if core_thread_id is not None else {}),
                },
            )

            # core.conversations.derive_messages is the only sanctioned way
            # to assemble conversation history (see that module's own
            # docstring) -- rebuilt fresh every turn.
            derived = derive_messages(conversation_id)
            if core_thread_id is not None:
                derived = [m for m in derived if m.thread_id == core_thread_id]
            history = [
                {
                    "role": "user" if m.event_type == "user/message" else "assistant",
                    "content": m.text,
                }
                for m in derived
            ]

            state = new_state(conversation_id, persona.id, thread_id=core_thread_id)
            state["messages"] = history

            placeholder = await context.bot.send_message(
                chat_id=chat.id,
                text=f"\U0001f4ad {persona.name} is thinking…",
                message_thread_id=thread_id,
            )
            await self.drive_graph(
                context.bot, chat.id, thread_id, conversation_id, state, placeholder
            )

    async def on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        # Mandatory per the verified research (§3) -- some Telegram clients
        # show a stuck loading spinner on the button otherwise.
        await query.answer()

        decoded = decode_callback_data(query.data or "")
        if decoded is None:
            return
        decision_str, request_id = decoded
        approved = decision_str == _APPROVE

        chat = query.message.chat
        conversation_id = conversation_id_for_chat(chat.id)
        thread_id = forum_thread_id(getattr(chat, "is_forum", False), query.message.message_thread_id)
        config = {"configurable": {"thread_id": conversation_id}}

        async with self._get_lock(conversation_id):
            snapshot = await self.graph.aget_state(config)
            current_request_id = (
                snapshot.interrupts[0].value.get("request_id") if snapshot.interrupts else None
            )
            if current_request_id != request_id:
                with contextlib.suppress(TelegramError):
                    suffix = "\n\n(Already handled, or unknown request.)"
                    await query.edit_message_text(
                        text=(query.message.text + suffix) if query.message.text else suffix.strip()
                    )
                return

            decided_by = update.effective_user.full_name if update.effective_user else "someone"
            decision_word = "APPROVED" if approved else "REJECTED"
            # query.message.text is the RENDERED text (HTML entities
            # already stripped by Telegram) -- no parse_mode here, so the
            # raw decision suffix can't be misinterpreted as markup (see
            # identity.py's judgment-call note).
            with contextlib.suppress(TelegramError):
                suffix = f"\n\nDecision: {decision_word} by {decided_by}"
                await query.edit_message_text(
                    text=(query.message.text + suffix) if query.message.text else suffix.strip()
                )

            placeholder = await context.bot.send_message(
                chat_id=chat.id, text="⏳ Resuming…", message_thread_id=thread_id
            )
            await self.drive_graph(
                context.bot,
                chat.id,
                thread_id,
                conversation_id,
                Command(resume=approved),
                placeholder,
            )

    # -- application wiring --------------------------------------------------------

    def build_application(self, token: str) -> Application:
        application = Application.builder().token(token).build()
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        application.add_handler(
            CallbackQueryHandler(self.on_callback_query, pattern=r"^(approve|reject):")
        )
        return application


async def start(token: str, *, checkpoint_path: str | None = None) -> Application:
    """Build the graph, wire up a `TelegramAdapter`, and start receiving
    updates -- as a sibling task alongside Discord/web on one shared event
    loop, per `project_structure.md`'s `main.py` note. See the module
    docstring's "Update-delivery mechanism" section for exactly why this is
    manual-lifecycle polling rather than `Application.run_polling()`.

    Does not block: `Updater.start_polling()` starts its own background
    fetch loop and returns immediately. Returns the running `Application`
    so a caller (`main.py`, or a test) can hold onto it for graceful
    shutdown via `stop()`.
    """
    graph = await build_graph(checkpoint_path)
    adapter = TelegramAdapter(graph)
    application = adapter.build_application(token)
    application.bot_data["tapestry_adapter"] = adapter

    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    return application


async def stop(application: Application) -> None:
    """The mirror image of `start()` -- shut down polling and the
    application cleanly, including the graph's own checkpointer connection
    (mirrors `discord_adapter.bot.start`'s `finally: await graph.
    checkpointer.conn.close()`). Safe to call even if `application`'s
    updater was never started.
    """
    # Captured BEFORE the teardown sequence below -- `Application.
    # shutdown()` is documented to release resources it owns, and
    # `bot_data` is one of them; reading it afterward risks finding it
    # already cleared, which would silently skip closing the checkpointer
    # connection.
    adapter = application.bot_data.get("tapestry_adapter")

    updater = application.updater
    if updater is not None and updater.running:
        await updater.stop()
    if application.running:
        await application.stop()
    await application.shutdown()

    if adapter is not None:
        await adapter.graph.checkpointer.conn.close()
