"""The Discord adapter's bot -- the first chat surface to go live per
`tapestry_scoped_spec.md`'s build sequence ("Discord first -- the webhook
trick proves the persona/event model cheaply").

Run as `await start(token)` -- **never** `bot.run()`. Verified
(`docs/vendor-research/ANALYSIS-discordpy.md` §5, `discord/client.py:853`):
`Client.run()` wraps its own `asyncio.run()` internally, which would
conflict with `main.py` running this adapter as a sibling task alongside
Telegram/web on one shared event loop.

Judgment call: `discord.Client`, not `discord.ext.commands.Bot`
------------------------------------------------------------------
`ANALYSIS-discordpy.md`'s own sketch uses `commands.Bot`, but that's an
illustrative choice in that doc, not a verified requirement -- the actual
verified facts are the individual signatures (`Webhook.send`,
`TextChannel.create_webhook`, `Message.create_thread`, `discord.ui.View`/
`button`, `Client.add_view`, `Client.start`), all confirmed directly
against the installed `discord.py==2.7.1` (see inline citations below).
This adapter never registers a prefix or slash command -- only raw
`on_message` routing plus persistent approval-button views -- so
`commands.Bot`'s command-prefix dispatch machinery would be pure unused
surface area (and an arbitrary, never-triggered `command_prefix` string
to boot). Plain `discord.Client` is what the routing logic actually needs
of the library.

Judgment call: default persona when nothing is named
-------------------------------------------------------
Discord's real `@mention` can only ever target the bot's own single
application user -- personas aren't separate bot accounts, that's the
entire point of the webhook-identity trick -- so *which persona* answers
has to come from the message text itself, not a real Discord mention.
`resolve_persona_id` matches a persona's `name` as the message's first
word (case-insensitive); when nothing matches, `DEFAULT_PERSONA_ID`
("ada") answers instead. Ada was picked deliberately: per `ada.yaml` she
holds only read-only tools (`file_editor_read`, `terminal_read_only`) and
her whole `system_prompt` is "plan before you code ... hand finished
plans to Rex ... delegate ... call out open questions" -- i.e. she is
already the persona whose job is triage/routing/delegation. An unrouted
"@Tapestry can someone look at X" lands on the one persona that cannot
mutate anything by itself and whose entire role is figuring out who
should. A future per-conversation default (via the `personas/`
management screen in `project_structure.md`) is the obvious place to make
this configurable instead of one hardcoded module constant.

Judgment call: thread spin-off is human-triggered only
-----------------------------------------------------------
The scoped spec's "human wants to spin off a focused sub-discussion" is
wired via an explicit `/thread` prefix in the human's own message (same
"predictable escape hatch" shape as the skills system's `/skill-name`
gesture) -- `message.create_thread(...)` is called and the whole turn
(placeholder + persona reply) plays out inside the new
`discord.Thread`, with `state["thread_id"]` set to that thread's id.
"A persona wants to spin off a thread" would need a new `TOOL_REGISTRY`
entry in `graph/build.py`, which is out of this adapter's scope (that
module is already built and tested) -- flagged here as a real gap for
whoever owns `graph/build.py` next, not silently worked around.

Naming collision, restated
----------------------------
`config["configurable"]["thread_id"]` below is LangGraph's own
checkpoint/session identity -- always our `conversation_id`. Our own
UI-thread concept lives entirely in `state["thread_id"]` /
`new_state(..., thread_id=...)`, a completely different field. Every use
of the word "thread" in this module's Discord-facing code (`discord.
Thread`, `Message.create_thread`) maps onto the *second* meaning, never
the first.
"""

from __future__ import annotations

import json
import re
import string
from datetime import datetime, timezone

import discord
from langgraph.types import Command

from tapestry.adapters.discord_adapter import webhook_identity
from tapestry.core import events
from tapestry.core.personas import Persona
from tapestry.graph.build import PERSONAS, build_graph, new_state
from tapestry.storage.db import get_connection

__all__ = [
    "DEFAULT_PERSONA_ID",
    "THREAD_COMMAND",
    "conversation_id_for_channel",
    "thread_id_for_channel",
    "resolve_persona_id",
    "strip_bot_mention",
    "strip_thread_command",
    "ApproveReject",
    "TapestryDiscordClient",
    "start",
]

# See "Judgment call: default persona when nothing is named" above.
DEFAULT_PERSONA_ID = "ada"

# The escape hatch that spins a human message off into a Discord thread —
# see "Judgment call: thread spin-off is human-triggered only" above.
THREAD_COMMAND = "/thread"

_MENTION_RE = re.compile(r"<@!?(\d+)>")

_APPROVE = "approve"
_REJECT = "reject"
_CUSTOM_ID_PREFIX = "tapestry"


# ---------------------------------------------------------------------------
# Pure helpers — conversation/thread/persona resolution, custom_id
# encode/decode, stream-frame rendering. Deliberately free functions (not
# methods) so they're testable without constructing a real discord.Client.
# ---------------------------------------------------------------------------


def conversation_id_for_channel(channel: object) -> str:
    """Deterministic `conversation_id` for a Discord channel/DM/thread.

    A `discord.Thread` maps back to its PARENT channel's conversation —
    Discord's own thread feature is the transport for OUR
    `state["thread_id"]` sub-discussion concept (see
    `thread_id_for_channel` below), not a second, separate conversation.
    Every other channel type (a guild text channel, a DM channel) is its
    own conversation, keyed by its own snowflake id.
    """
    if isinstance(channel, discord.Thread):
        return f"discord-{channel.parent_id}"
    return f"discord-{channel.id}"


def thread_id_for_channel(channel: object) -> str | None:
    """The `state["thread_id"]` value implied by `channel`, or None."""
    if isinstance(channel, discord.Thread):
        return str(channel.id)
    return None


def resolve_persona_id(content: str) -> str:
    """Pick which persona answers, from the message text alone.

    Convention: the message's first word, stripped of surrounding
    punctuation, matched case-insensitively against a persona's `name`
    (e.g. "Rex, can you ..." routes to Rex). Falls back to
    `DEFAULT_PERSONA_ID` when nothing matches, including an empty
    message.
    """
    stripped = content.strip()
    if not stripped:
        return DEFAULT_PERSONA_ID
    first_word = stripped.split(maxsplit=1)[0].strip(string.punctuation)
    for persona in PERSONAS.values():
        if persona.name.lower() == first_word.lower():
            return persona.id
    return DEFAULT_PERSONA_ID


def strip_bot_mention(content: str, bot_user_id: int) -> str:
    """Remove the bot's own `<@id>`/`<@!id>` mention text, if present."""
    def _replace(match: re.Match[str]) -> str:
        return "" if int(match.group(1)) == bot_user_id else match.group(0)

    return _MENTION_RE.sub(_replace, content).strip()


def strip_thread_command(content: str) -> tuple[str, bool]:
    """Peel a leading `THREAD_COMMAND` off `content`.

    Returns `(remaining_text, wants_thread)`.
    """
    stripped = content.strip()
    if stripped.lower().startswith(THREAD_COMMAND):
        return stripped[len(THREAD_COMMAND):].strip(), True
    return stripped, False


def _thread_name(content: str) -> str:
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    name = first_line[:80].strip()
    return name or "Tapestry thread"


def approval_custom_id(action: str, conversation_id: str, request_id: str) -> str:
    """Stable `custom_id` for one approve/reject button.

    Incorporates `conversation_id`/`request_id` (rather than being fixed
    at class-decoration time) so a per-request `ApproveReject` view can be
    re-registered via `Client.add_view()` at startup and still route a
    click to the right pending approval after a bot restart — see
    `ApproveReject`/`_register_pending_approval_views` below.
    """
    return f"{_CUSTOM_ID_PREFIX}:{action}:{conversation_id}:{request_id}"


def decode_approval_custom_id(custom_id: str) -> tuple[str, str, str] | None:
    """Inverse of `approval_custom_id`. None if `custom_id` isn't ours.

    `conversation_id` is always shaped `discord-<snowflake>` (no colons),
    and `request_id` is a `uuid4().hex` (no colons either), so splitting
    on `:` with `maxsplit=3` recovers all four fields unambiguously.
    """
    parts = custom_id.split(":", 3)
    if len(parts) != 4 or parts[0] != _CUSTOM_ID_PREFIX or parts[1] not in (_APPROVE, _REJECT):
        return None
    _, action, conversation_id, request_id = parts
    return action, conversation_id, request_id


def render_stream_frame(frame: dict) -> str | None:
    """Turn one `graph.streaming.emit(...)` frame into placeholder text.

    Returns None for a frame that shouldn't cause an edit (an unknown
    frame type, or one this adapter deliberately doesn't render).
    """
    event_type = frame.get("type")
    payload = frame.get("payload") or {}
    if event_type == "persona/thinking":
        persona = PERSONAS.get(payload.get("persona_id"))
        name = persona.name if persona else payload.get("persona_id", "Someone")
        return f"_{name} is thinking…_"
    if event_type == "tool/status":
        tool_name = payload.get("tool_name", "tool")
        status = payload.get("status")
        if status == "running":
            return f"_Running `{tool_name}`…_"
        if status == "done":
            outcome = "failed" if payload.get("is_error") else "finished"
            return f"_`{tool_name}` {outcome}._"
    return None


def last_assistant_text(messages: list[dict]) -> str:
    """The most recent assistant-authored text in a graph state's
    `messages` list — what actually gets posted as the persona's final
    reply. Empty string if there isn't one (defensive; shouldn't happen
    for a turn that reached `next_node == "end"`).
    """
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            if content:
                return content
    return ""


def pending_approval_request_ids(conversation_id: str) -> list[str]:
    """`ask/requested` ids in this conversation with `intent == "approval"`
    and no matching `ask/answered` yet — i.e. every approval a human still
    owes a click on. Used at startup to re-register persistent views after
    a restart (see `TapestryDiscordClient._register_pending_approval_views`).
    """
    requested: dict[str, None] = {}
    answered: set[str] = set()
    for event in events.read_events(conversation_id):
        if event.type == "ask/requested":
            for question in event.payload.get("questions", []):
                if question.get("intent") == "approval" and "id" in question:
                    requested[question["id"]] = None
        elif event.type == "ask/answered":
            request_id = event.payload.get("request_id")
            if request_id is not None:
                answered.add(request_id)
    return [request_id for request_id in requested if request_id not in answered]


def ensure_conversation_row(conversation_id: str, kind: str, name: str | None = None) -> None:
    """Create `conversations`' row for `conversation_id` if it doesn't
    already exist. Schema per `storage/schema.sql`:
    `conversations(id, kind, name, created_at)`, `kind IN ('dm', 'group')`.
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


def _all_conversation_ids() -> list[str]:
    conn = get_connection()
    rows = conn.execute("SELECT id FROM conversations").fetchall()
    return [row[0] for row in rows]


def _webhook_target(channel: object) -> tuple[object, dict[str, object]]:
    """The channel to fetch/create the persona webhook against, plus any
    extra `Webhook.send` kwargs needed to land the message in the right
    place. A webhook belongs to a channel, never a thread directly —
    posting INTO a `discord.Thread` means sending through the *parent*
    channel's webhook with `thread=<the Thread>` (verified:
    `Webhook.send`'s `thread: Snowflake = MISSING` kwarg,
    `docs/vendor-research/ANALYSIS-discordpy.md` §1).
    """
    if isinstance(channel, discord.Thread):
        return channel.parent, {"thread": channel}
    return channel, {}


# ---------------------------------------------------------------------------
# Persistent approve/reject view
# ---------------------------------------------------------------------------


class ApproveReject(discord.ui.View):
    """Approve/reject buttons for one pending tool-approval `interrupt()`.

    `timeout=None` plus an explicit, non-decorator-fixed `custom_id` per
    button (assigned in `__init__`, one embedding this specific
    `conversation_id`/`request_id`) is what makes `view.is_persistent()`
    true and lets `Client.add_view(view)` (no `message_id` needed —
    verified: `discord/client.py`'s `add_view` docstring says
    `message_id` is only used for message-update-event propagation, not
    required for interaction dispatch) route a click back to the right
    pending approval, including one created by a PREVIOUS process run —
    see `TapestryDiscordClient.setup_hook`.
    """

    def __init__(self, client: "TapestryDiscordClient", conversation_id: str, request_id: str) -> None:
        super().__init__(timeout=None)
        self.client = client
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.approve.custom_id = approval_custom_id(_APPROVE, conversation_id, request_id)
        self.reject.custom_id = approval_custom_id(_REJECT, conversation_id, request_id)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.client.handle_approval_click(
            interaction, self.conversation_id, self.request_id, approved=True
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.client.handle_approval_click(
            interaction, self.conversation_id, self.request_id, approved=False
        )


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


def _build_intents() -> discord.Intents:
    # `Intents.default()` already includes `guild_messages`/`dm_messages`;
    # `message_content` is the one privileged intent this adapter needs
    # and must enable explicitly (verified requirement,
    # `docs/vendor-research/ANALYSIS-discordpy.md`'s recommendation block).
    intents = discord.Intents.default()
    intents.message_content = True
    return intents


class TapestryDiscordClient(discord.Client):
    """The bot. One instance, one `graph` shared across every
    conversation — different conversations are just different LangGraph
    `thread_id`s against the same compiled graph/checkpointer.

    Known limitation, not addressed here: nothing serializes two
    messages that land in the SAME channel close together into one
    queue. Two human messages fired at a channel back to back start two
    concurrent `astream()` calls against the same LangGraph `thread_id`,
    which race on that conversation's checkpoint. A per-conversation
    lock (or a single-worker queue keyed by `conversation_id`) would
    close this, but is out of scope for the first working version of
    this adapter — flagged here rather than silently assumed away.
    """

    def __init__(self, *, graph, intents: discord.Intents | None = None, **options: object) -> None:
        super().__init__(intents=intents or _build_intents(), **options)
        self.graph = graph
        # In-memory only, per project_structure.md's own pattern for
        # close_orphaned_turns — tracks which conversations this PROCESS
        # has already run startup repair on, so it's not re-run on every
        # message. Deliberately not persisted: a fresh process should
        # always re-check every conversation it touches for real.
        self._known_conversations: set[str] = set()
        self._in_flight_resumes: set[tuple[str, str]] = set()
        # In-memory guard against the concurrency bug documented in
        # `tapestry_mentions_concurrency_status_spec.md` §1 -- mirrors
        # web_adapter/api.py's `app.state.turns_in_flight`. Closes the
        # narrow window a pure event-log read can't: two near-simultaneous
        # messages before the first turn's own `turn/start` event even
        # lands in the log. `drive_graph` clears an entry in a `finally` on
        # every exit path.
        self._turns_in_flight: set[str] = set()

    # -- startup -------------------------------------------------------

    async def setup_hook(self) -> None:
        """Called once, after login but before the websocket connects —
        the documented place to register persistent views. Re-registers
        an `ApproveReject` view for every approval still awaiting a human
        click across every known conversation, so a click on a message
        posted by a PREVIOUS process run still resolves correctly.

        Deliberately does NOT call `events.close_orphaned_turns` here.
        A conversation with a pending approval is, by definition, paused
        at a real `interrupt()` — its open `turn/start` is not an orphan
        (the process didn't crash mid-turn, it's mid-approval on
        purpose), and repairing it here would append a false
        `"interrupted"` `turn/end` onto a turn that's about to
        legitimately continue the moment a human clicks. Orphan repair
        still happens per conversation, once, the first time a live
        message routes through `ensure_conversation` below — this
        startup pass only re-attaches button listeners, nothing more.
        """
        for conversation_id in _all_conversation_ids():
            for request_id in pending_approval_request_ids(conversation_id):
                self.add_view(ApproveReject(self, conversation_id, request_id))

    async def ensure_conversation(self, conversation_id: str, *, is_dm: bool) -> None:
        if conversation_id in self._known_conversations:
            return
        ensure_conversation_row(conversation_id, "dm" if is_dm else "group")
        events.close_orphaned_turns(conversation_id)
        self._known_conversations.add(conversation_id)

    # -- inbound messages ------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        # Never react to our own messages, another bot, or a persona's
        # own webhook-posted message (webhooks fire MESSAGE_CREATE too —
        # without this guard a persona's reply could be misread as a new
        # human turn).
        if message.author.bot or message.webhook_id is not None:
            return
        if self.user is None:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.user in message.mentions
        if not (is_dm or is_mentioned):
            return

        content = strip_bot_mention(message.content, self.user.id)
        content, wants_thread = strip_thread_command(content)
        if not content:
            return

        channel: object = message.channel
        if wants_thread and not isinstance(channel, discord.Thread):
            try:
                channel = await message.create_thread(name=_thread_name(content))
            except discord.HTTPException:
                pass  # fall back to answering in the original channel

        conversation_id = conversation_id_for_channel(channel)
        thread_id = thread_id_for_channel(channel)
        await self.ensure_conversation(conversation_id, is_dm=is_dm)

        persona_id = resolve_persona_id(content)

        # Paused-persona gate -- see web_adapter/api.py's
        # `_reject_if_persona_paused` and
        # tapestry_mentions_concurrency_status_spec.md §4/§5 decision 2:
        # `status == "paused"` must actually block a turn, not just display
        # as paused (nova.yaml ships paused deliberately -- her own
        # system_prompt requires explicit human activation first).
        paused_persona = PERSONAS.get(persona_id)
        if paused_persona is not None and paused_persona.status == "paused":
            await channel.send(
                f"_{paused_persona.name} is paused -- resume them before messaging._"
            )
            return

        # Concurrency guard -- see web_adapter/api.py's
        # `_reject_if_turn_in_progress` and
        # tapestry_mentions_concurrency_status_spec.md §1: a fresh
        # `new_state()` on a conversation that already has a turn running or
        # paused at an approval silently clobbers the LangGraph checkpoint
        # instead of erroring. `ensure_conversation` just above only repairs
        # CRASHED turns; a turn open here is real, in-flight-or-paused
        # state. Two checks, same split as the web adapter's: the in-memory
        # set catches this process actively mid-turn (including the window
        # before its `turn/start` event has even landed in the log); the
        # log scan catches a turn left open by a previous process.
        if conversation_id in self._turns_in_flight:
            await channel.send(
                "_Still working on the last message here -- send this again in a "
                "moment._"
            )
            return
        # Filtered to the MAIN thread only -- mirrors web_adapter/api.py's
        # equivalent guard (spec §2.2). Tag-all fan-out itself is only
        # built for the web adapter's group conversations (Discord's own
        # conversation ids are a disjoint namespace, so it can never
        # actually see a fan-out-leg turn/start today) -- kept here anyway
        # so this guard shares one correct idiom with the web adapter
        # rather than silently drifting if that ever changes.
        open_turns = [
            e
            for e in events.find_open_turns(events.read_events(conversation_id)).values()
            if events.is_main_thread_turn(e, conversation_id)
        ]
        if open_turns:
            stalled_persona = open_turns[-1].actor
            await channel.send(
                f"_{stalled_persona} is still working on the last message (or waiting "
                "on your approval) -- send this again once that's done._"
            )
            return
        # Marked synchronously, no `await` since the checks above -- so a
        # second near-simultaneous on_message can't slip through before
        # this one's `turn/start` lands in the log. `drive_graph` clears it
        # in a `finally` on every exit path.
        self._turns_in_flight.add(conversation_id)

        payload = {"text": content}
        if thread_id:
            payload["thread_id"] = thread_id
        events.append_event(conversation_id, "user/message", actor="human", payload=payload)

        persona = PERSONAS.get(persona_id)
        persona_label = persona.name if persona else persona_id
        placeholder = await channel.send(f"_{persona_label} is thinking…_")

        state = new_state(conversation_id, persona_id, thread_id=thread_id)
        await self.drive_graph(channel, conversation_id, state, placeholder)

    # -- approval clicks ---------------------------------------------------

    async def handle_approval_click(
        self,
        interaction: discord.Interaction,
        conversation_id: str,
        request_id: str,
        *,
        approved: bool,
    ) -> None:
        key = (conversation_id, request_id)
        if key in self._in_flight_resumes:
            await interaction.response.send_message(
                "Already processing this decision…", ephemeral=True
            )
            return

        config = {"configurable": {"thread_id": conversation_id}}
        snapshot = await self.graph.aget_state(config)
        current_request_id = None
        if snapshot.interrupts:
            current_request_id = snapshot.interrupts[0].value.get("request_id")
        if current_request_id != request_id:
            await interaction.response.send_message(
                "This request has already been handled.", ephemeral=True
            )
            return

        self._in_flight_resumes.add(key)
        try:
            decision_word = "Approved" if approved else "Rejected"
            original = interaction.message.content if interaction.message else ""
            await interaction.response.edit_message(
                content=f"{original}\n\n**{decision_word}** by {interaction.user.display_name}.",
                view=None,
            )

            channel = interaction.channel
            placeholder = await channel.send("_…continuing…_")
            await self.drive_graph(
                channel, conversation_id, Command(resume=approved), placeholder
            )
        finally:
            self._in_flight_resumes.discard(key)

    # -- driving the graph ---------------------------------------------

    async def drive_graph(
        self,
        channel: object,
        conversation_id: str,
        graph_input: object,
        placeholder: discord.Message,
    ) -> None:
        """Run (or resume) one graph step to completion or the next
        pause, forwarding `graph.streaming.emit(...)` frames onto
        `placeholder` as message edits, then either posting a new
        approval prompt (paused) or the persona's final reply via the
        shared webhook (finished).

        `graph/build.py` deliberately RAISES for several real, expected
        conditions rather than swallowing them: `TurnBudgetExceeded`
        (checked first thing in `persona_node`), `DelegationDepthExceeded`
        / `DelegationRoundLimitExceeded` (re-raised by `_handle_delegate`
        after closing the turn — see `test_build.py::
        test_delegation_depth_exceeded_closes_the_turn_then_raises`), and
        whatever `models.litellm_client.call_model` or `_get_persona`
        raise on a bad model/persona config. Left unguarded, any of these
        would propagate out of `on_message`/`handle_approval_click`,
        which discord.py logs and swallows — leaving the human staring at
        "_...is thinking..._" forever with no reply and no visible error.
        Catching broadly here and turning the failure into an edited,
        visible placeholder message is deliberate: a chat surface must
        never go silently unresponsive.
        """
        config = {"configurable": {"thread_id": conversation_id}}

        try:
            try:
                async for frame in self.graph.astream(
                    graph_input, config=config, stream_mode="custom"
                ):
                    await self._apply_stream_frame(placeholder, frame)
                snapshot = await self.graph.aget_state(config)
            except Exception as exc:  # noqa: BLE001 -- see docstring above
                await self._apply_error(placeholder, exc)
                return

            persona_id = snapshot.values.get("persona_id", DEFAULT_PERSONA_ID)
            persona = PERSONAS.get(persona_id)

            if snapshot.interrupts:
                await self._post_approval_request(
                    channel, conversation_id, persona, snapshot.interrupts[0], placeholder
                )
                return

            final_text = last_assistant_text(snapshot.values.get("messages", [])) or (
                "(no response)"
            )
            try:
                await placeholder.delete()
            except discord.HTTPException:
                pass

            if persona is None:
                await channel.send(final_text)
                return

            webhook_channel, extra_kwargs = _webhook_target(channel)
            await webhook_identity.post_as_persona(
                webhook_channel, persona, final_text, **extra_kwargs
            )
        finally:
            # Mirrors web_adapter/api.py's `_drive_turn` finally: this
            # conversation is no longer "actively executing in this
            # process" once this function returns, whatever the reason --
            # see `on_message`'s `_turns_in_flight` guard docstring.
            self._turns_in_flight.discard(conversation_id)

    async def _apply_stream_frame(self, placeholder: discord.Message, frame: dict) -> None:
        text = render_stream_frame(frame)
        if text is None:
            return
        try:
            await placeholder.edit(content=text)
        except discord.HTTPException:
            pass

    async def _apply_error(self, placeholder: discord.Message, exc: Exception) -> None:
        """Turn an exception from driving the graph into a visible,
        edited placeholder message instead of a silent hang. See
        `drive_graph`'s docstring for why this exists.
        """
        try:
            await placeholder.edit(content=f"_Something went wrong: {type(exc).__name__}._")
        except discord.HTTPException:
            pass

    async def _post_approval_request(
        self,
        channel: object,
        conversation_id: str,
        persona: Persona | None,
        interrupt_obj: object,
        placeholder: discord.Message,
    ) -> None:
        value = interrupt_obj.value
        request_id = value.get("request_id", "unknown")
        tool_name = value.get("tool_name")
        arguments = value.get("arguments") or {}
        persona_label = persona.name if persona else "A persona"
        detail = json.dumps(arguments, indent=2, sort_keys=True)[:1500]
        content = f"**{persona_label}** wants to run `{tool_name}` — approve?\n```{detail}```"

        try:
            await placeholder.delete()
        except discord.HTTPException:
            pass

        view = ApproveReject(self, conversation_id, request_id)
        await channel.send(content=content, view=view)


async def start(token: str, *, checkpoint_path: str | None = None) -> None:
    """Entry point: build the graph once, then run the client until it
    disconnects. `main.py` awaits this as a sibling task alongside the
    other adapters, on one shared event loop — see module docstring for
    why this is `client.start(token)`, never `client.run(token)`.
    """
    graph = await build_graph(checkpoint_path)
    client = TapestryDiscordClient(graph=graph)
    try:
        await client.start(token)
    finally:
        await graph.checkpointer.conn.close()
