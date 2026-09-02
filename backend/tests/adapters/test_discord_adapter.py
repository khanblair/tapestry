"""Tests for the Discord adapter (`bot.py` / `webhook_identity.py`).

`discord.py`'s real client/webhook classes are never constructed for
real (no gateway connection, no HTTP calls) -- every discord.py object
used here is either a `unittest.mock.MagicMock(spec=...)` (so
`isinstance()` checks against `discord.DMChannel`/`discord.Thread`/etc.
still behave correctly, per `unittest.mock`'s own `spec=` contract) or a
lightweight duck-typed stand-in. `TapestryDiscordClient` itself is
constructed via `__new__` (bypassing `discord.Client.__init__`, which
needs a real event loop/gateway state) with only the attributes its own
methods actually read set by hand -- this exercises the REAL bound
methods (`on_message`, `drive_graph`, `handle_approval_click`, ...), not
mocks of them, while sidestepping discord.py's own connection machinery.

`core.events`/the `conversations` table are exercised for real against
an isolated in-memory SQLite connection (see `conftest.py` in this
package) -- covers "the message -> event append flow" against the
genuine event-log mechanics, not a mock of `append_event`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from langgraph.types import Command

from tapestry.adapters.discord_adapter import bot, webhook_identity
from tapestry.core import events as events_module
from tapestry.core.personas import Persona
from tapestry.graph.budgets import TurnBudgetExceeded
from tapestry.graph.build import PERSONAS as REAL_PERSONAS

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _persona(persona_id: str, name: str, color: str = "#3B82F6") -> Persona:
    return Persona(
        id=persona_id,
        name=name,
        role="Tester",
        model="claude-opus-4-6",
        system_prompt="be helpful",
        tools=[],
        mcp_servers=[],
        status="online",
        color=color,
    )


ADA = _persona("ada", "Ada", "#3B82F6")
REX = _persona("rex", "Rex", "#EF4444")


@pytest.fixture(autouse=True)
def personas(monkeypatch):
    """Point `bot.PERSONAS` at a small, deterministic fixture roster
    instead of whatever `personas/*.yaml` happens to contain, so persona
    resolution tests don't depend on repo content.
    """
    fixture = {"ada": ADA, "rex": REX}
    monkeypatch.setattr(bot, "PERSONAS", fixture)
    yield fixture


def make_client(graph: object | None = None) -> bot.TapestryDiscordClient:
    """A real `TapestryDiscordClient` instance, built without going
    through `discord.Client.__init__` (which needs a live event loop /
    gateway state we don't have in a unit test) -- see module docstring.
    """
    client = bot.TapestryDiscordClient.__new__(bot.TapestryDiscordClient)
    client.graph = graph
    client._known_conversations = set()
    client._in_flight_resumes = set()
    client._connection = SimpleNamespace(user=SimpleNamespace(id=999))
    client.add_view = MagicMock()
    return client


def make_channel(spec_cls: type, channel_id: int, **extra: object) -> MagicMock:
    channel = MagicMock(spec=spec_cls)
    channel.id = channel_id
    channel.send = AsyncMock()
    for key, value in extra.items():
        setattr(channel, key, value)
    return channel


def make_message(
    content: str,
    *,
    channel: object,
    author_bot: bool = False,
    webhook_id: int | None = None,
    mentions: list[object] | None = None,
) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.content = content
    message.channel = channel
    message.author = MagicMock()
    message.author.bot = author_bot
    message.webhook_id = webhook_id
    message.mentions = mentions or []
    message.create_thread = AsyncMock()
    return message


class FakeGraph:
    """Duck-typed stand-in for `CompiledStateGraph` -- records every
    `astream`/`aget_state` call and replays a scripted response.
    """

    def __init__(self, frames: list[dict], snapshot: object, error: Exception | None = None) -> None:
        self._frames = frames
        self._snapshot = snapshot
        self._error = error
        self.astream_calls: list[tuple] = []
        self.aget_state_calls: list[dict] = []

    def astream(self, graph_input, config, stream_mode):
        self.astream_calls.append((graph_input, config, stream_mode))

        async def _generator():
            for frame in self._frames:
                yield frame
            if self._error is not None:
                raise self._error

        return _generator()

    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        return self._snapshot


def make_snapshot(values: dict, interrupts: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(values=values, interrupts=interrupts)


def make_interrupt(**value: object) -> SimpleNamespace:
    return SimpleNamespace(value=value)


# ===========================================================================
# Pure helpers: conversation_id / thread_id mapping
# ===========================================================================


class TestConversationIdMapping:
    def test_text_channel_maps_to_discord_prefixed_id(self):
        channel = make_channel(discord.TextChannel, 111)
        assert bot.conversation_id_for_channel(channel) == "discord-111"

    def test_dm_channel_maps_to_discord_prefixed_id(self):
        channel = make_channel(discord.DMChannel, 222)
        assert bot.conversation_id_for_channel(channel) == "discord-222"

    def test_thread_maps_back_to_parent_channel_conversation(self):
        thread = make_channel(discord.Thread, 999, parent_id=111)
        assert bot.conversation_id_for_channel(thread) == "discord-111"

    def test_thread_id_for_channel_is_none_outside_a_thread(self):
        channel = make_channel(discord.TextChannel, 111)
        assert bot.thread_id_for_channel(channel) is None

    def test_thread_id_for_channel_is_the_threads_own_id(self):
        thread = make_channel(discord.Thread, 999, parent_id=111)
        assert bot.thread_id_for_channel(thread) == "999"


# ===========================================================================
# Pure helpers: persona resolution
# ===========================================================================


class TestPersonaResolution:
    def test_matches_persona_name_as_first_word(self):
        assert bot.resolve_persona_id("Rex can you look at this") == "rex"

    def test_case_insensitive(self):
        assert bot.resolve_persona_id("rex please help") == "rex"

    def test_strips_punctuation_before_matching(self):
        assert bot.resolve_persona_id("Rex, please help") == "rex"

    def test_falls_back_to_default_when_no_persona_named(self):
        assert bot.resolve_persona_id("can someone help with this") == bot.DEFAULT_PERSONA_ID

    def test_falls_back_to_default_on_empty_message(self):
        assert bot.resolve_persona_id("   ") == bot.DEFAULT_PERSONA_ID

    def test_default_persona_is_ada(self):
        # Documented judgment call -- see bot.py's module docstring.
        assert bot.DEFAULT_PERSONA_ID == "ada"

    def test_default_persona_id_is_a_real_persona_in_the_actual_roster(self):
        # Deliberately against the REAL tapestry.graph.build.PERSONAS,
        # not the small fixture roster the `personas` autouse fixture
        # substitutes into `bot.PERSONAS` for the rest of this file --
        # if ada.yaml is ever renamed/removed, PERSONAS.get("ada") goes
        # None and new_state(..., "ada") -> persona_node -> _get_persona
        # raises KeyError, which is exactly the silent-hang failure mode
        # drive_graph's error handling exists to catch. This test is
        # what should catch the misconfiguration BEFORE that.
        assert bot.DEFAULT_PERSONA_ID in REAL_PERSONAS


# ===========================================================================
# Pure helpers: mention / thread-command stripping
# ===========================================================================


class TestContentStripping:
    def test_strip_bot_mention_removes_only_the_bots_own_mention(self):
        assert bot.strip_bot_mention("<@999> hello there", 999) == "hello there"

    def test_strip_bot_mention_leaves_other_mentions_alone(self):
        result = bot.strip_bot_mention("<@999> hi <@123>", 999)
        assert result == "hi <@123>"

    def test_strip_bot_mention_handles_nickname_mention_form(self):
        assert bot.strip_bot_mention("<@!999> hello", 999) == "hello"

    def test_strip_thread_command_detects_prefix(self):
        text, wants_thread = bot.strip_thread_command("/thread let's dig into this")
        assert wants_thread is True
        assert text == "let's dig into this"

    def test_strip_thread_command_is_case_insensitive(self):
        text, wants_thread = bot.strip_thread_command("/THREAD go")
        assert wants_thread is True
        assert text == "go"

    def test_strip_thread_command_absent_leaves_content_untouched(self):
        text, wants_thread = bot.strip_thread_command("just a normal message")
        assert wants_thread is False
        assert text == "just a normal message"


# ===========================================================================
# Pure helpers: approval custom_id encode/decode (resume-decision routing)
# ===========================================================================


class TestApprovalCustomId:
    def test_round_trips_approve(self):
        custom_id = bot.approval_custom_id("approve", "discord-111", "req-abc")
        assert bot.decode_approval_custom_id(custom_id) == ("approve", "discord-111", "req-abc")

    def test_round_trips_reject(self):
        custom_id = bot.approval_custom_id("reject", "discord-222", "req-xyz")
        assert bot.decode_approval_custom_id(custom_id) == ("reject", "discord-222", "req-xyz")

    def test_rejects_foreign_custom_id(self):
        assert bot.decode_approval_custom_id("something:else:entirely") is None

    def test_rejects_malformed_action(self):
        assert bot.decode_approval_custom_id("tapestry:maybe:discord-1:req-1") is None

    def test_distinct_requests_get_distinct_ids(self):
        a = bot.approval_custom_id("approve", "discord-1", "req-1")
        b = bot.approval_custom_id("approve", "discord-1", "req-2")
        assert a != b


# ===========================================================================
# Pure helpers: stream-frame rendering
# ===========================================================================


class TestRenderStreamFrame:
    def test_persona_thinking_renders_persona_name(self):
        frame = {"type": "persona/thinking", "payload": {"persona_id": "rex"}}
        assert bot.render_stream_frame(frame) == "_Rex is thinking…_"

    def test_persona_thinking_falls_back_to_raw_id_for_unknown_persona(self):
        frame = {"type": "persona/thinking", "payload": {"persona_id": "ghost"}}
        assert "ghost" in bot.render_stream_frame(frame)

    def test_tool_status_running(self):
        frame = {"type": "tool/status", "payload": {"tool_name": "terminal", "status": "running"}}
        assert "terminal" in bot.render_stream_frame(frame)
        assert "Running" in bot.render_stream_frame(frame)

    def test_tool_status_done_success(self):
        frame = {
            "type": "tool/status",
            "payload": {"tool_name": "terminal", "status": "done", "is_error": False},
        }
        assert "finished" in bot.render_stream_frame(frame)

    def test_tool_status_done_error(self):
        frame = {
            "type": "tool/status",
            "payload": {"tool_name": "terminal", "status": "done", "is_error": True},
        }
        assert "failed" in bot.render_stream_frame(frame)

    def test_unknown_frame_type_renders_nothing(self):
        frame = {"type": "persona/responded", "payload": {}}
        assert bot.render_stream_frame(frame) is None


# ===========================================================================
# Pure helpers: last_assistant_text
# ===========================================================================


class TestLastAssistantText:
    def test_returns_the_most_recent_assistant_message(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "first reply"},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "final reply"},
        ]
        assert bot.last_assistant_text(messages) == "final reply"

    def test_empty_when_no_assistant_message(self):
        assert bot.last_assistant_text([{"role": "user", "content": "hi"}]) == ""

    def test_skips_assistant_messages_with_empty_content(self):
        messages = [
            {"role": "assistant", "content": "real answer"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
        ]
        assert bot.last_assistant_text(messages) == "real answer"


# ===========================================================================
# ensure_conversation_row / conversation creation
# ===========================================================================


class TestEnsureConversationRow:
    def test_creates_a_row(self):
        bot.ensure_conversation_row("discord-1", "group")
        conn = bot.get_connection()
        row = conn.execute("SELECT id, kind FROM conversations WHERE id = ?", ("discord-1",)).fetchone()
        assert row[0] == "discord-1"
        assert row[1] == "group"

    def test_is_idempotent(self):
        bot.ensure_conversation_row("discord-1", "group")
        bot.ensure_conversation_row("discord-1", "group")  # must not raise
        conn = bot.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE id = ?", ("discord-1",)
        ).fetchone()[0]
        assert count == 1


# ===========================================================================
# pending_approval_request_ids -- startup recovery data source
# ===========================================================================


class TestPendingApprovalRequestIds:
    def test_empty_when_nothing_asked(self):
        assert bot.pending_approval_request_ids("discord-1") == []

    def test_includes_unanswered_approval_request(self):
        events_module.append_event(
            "discord-1",
            "ask/requested",
            actor="system",
            payload={"questions": [{"id": "req-1", "intent": "approval"}]},
        )
        assert bot.pending_approval_request_ids("discord-1") == ["req-1"]

    def test_excludes_answered_request(self):
        events_module.append_event(
            "discord-1",
            "ask/requested",
            actor="system",
            payload={"questions": [{"id": "req-1", "intent": "approval"}]},
        )
        events_module.append_event(
            "discord-1", "ask/answered", actor="human", payload={"request_id": "req-1", "answers": []}
        )
        assert bot.pending_approval_request_ids("discord-1") == []

    def test_ignores_non_approval_asks(self):
        events_module.append_event(
            "discord-1",
            "ask/requested",
            actor="system",
            payload={"questions": [{"id": "req-1", "intent": "clarification"}]},
        )
        assert bot.pending_approval_request_ids("discord-1") == []


# ===========================================================================
# webhook_identity
# ===========================================================================


class TestPersonaAvatarUrl:
    def test_deterministic_for_the_same_persona(self):
        assert webhook_identity.persona_avatar_url(ADA) == webhook_identity.persona_avatar_url(ADA)

    def test_differs_by_persona(self):
        assert webhook_identity.persona_avatar_url(ADA) != webhook_identity.persona_avatar_url(REX)

    def test_encodes_name_and_color(self):
        url = webhook_identity.persona_avatar_url(ADA)
        assert "Ada" in url
        assert "3B82F6" in url


class TestGetOrCreatePersonaWebhook:
    async def test_reuses_existing_tapestry_webhook(self):
        existing = MagicMock(spec=discord.Webhook)
        existing.name = webhook_identity.WEBHOOK_NAME
        other = MagicMock(spec=discord.Webhook)
        other.name = "some-other-hook"

        channel = MagicMock(spec=discord.TextChannel)
        channel.webhooks = AsyncMock(return_value=[other, existing])
        channel.create_webhook = AsyncMock()

        result = await webhook_identity.get_or_create_persona_webhook(channel)

        assert result is existing
        channel.create_webhook.assert_not_called()

    async def test_creates_when_absent(self):
        created = MagicMock(spec=discord.Webhook)
        channel = MagicMock(spec=discord.TextChannel)
        channel.webhooks = AsyncMock(return_value=[])
        channel.create_webhook = AsyncMock(return_value=created)

        result = await webhook_identity.get_or_create_persona_webhook(channel)

        assert result is created
        channel.create_webhook.assert_awaited_once_with(name=webhook_identity.WEBHOOK_NAME)


class TestPostAsPersona:
    async def test_sends_with_persona_identity_and_waits_for_the_message(self):
        webhook_message = MagicMock(spec=discord.WebhookMessage)
        webhook = MagicMock(spec=discord.Webhook)
        webhook.name = webhook_identity.WEBHOOK_NAME
        webhook.send = AsyncMock(return_value=webhook_message)

        channel = MagicMock(spec=discord.TextChannel)
        channel.webhooks = AsyncMock(return_value=[webhook])

        result = await webhook_identity.post_as_persona(channel, REX, "hello world")

        assert result is webhook_message
        webhook.send.assert_awaited_once()
        _, kwargs = webhook.send.await_args
        assert kwargs["content"] == "hello world"
        assert kwargs["username"] == "Rex"
        assert kwargs["avatar_url"] == webhook_identity.persona_avatar_url(REX)
        assert kwargs["wait"] is True

    async def test_kwargs_can_override_defaults_eg_for_a_thread(self):
        webhook_message = MagicMock(spec=discord.WebhookMessage)
        webhook = MagicMock(spec=discord.Webhook)
        webhook.name = webhook_identity.WEBHOOK_NAME
        webhook.send = AsyncMock(return_value=webhook_message)

        channel = MagicMock(spec=discord.TextChannel)
        channel.webhooks = AsyncMock(return_value=[webhook])
        fake_thread = object()

        await webhook_identity.post_as_persona(channel, REX, "hi", thread=fake_thread)

        _, kwargs = webhook.send.await_args
        assert kwargs["thread"] is fake_thread


# ===========================================================================
# on_message -- routing + the message -> event append flow
# ===========================================================================


class TestOnMessage:
    async def test_ignores_messages_from_bots(self):
        client = make_client()
        client.drive_graph = AsyncMock()
        channel = make_channel(discord.DMChannel, 1)
        message = make_message("hi", channel=channel, author_bot=True)

        await bot.TapestryDiscordClient.on_message(client, message)

        client.drive_graph.assert_not_called()
        channel.send.assert_not_called()

    async def test_ignores_persona_webhook_messages(self):
        client = make_client()
        client.drive_graph = AsyncMock()
        channel = make_channel(discord.TextChannel, 1)
        message = make_message("hi", channel=channel, webhook_id=555)

        await bot.TapestryDiscordClient.on_message(client, message)

        client.drive_graph.assert_not_called()

    async def test_ignores_non_dm_message_without_a_mention(self):
        client = make_client()
        client.drive_graph = AsyncMock()
        channel = make_channel(discord.TextChannel, 1)
        message = make_message("hi everyone", channel=channel, mentions=[])

        await bot.TapestryDiscordClient.on_message(client, message)

        client.drive_graph.assert_not_called()
        channel.send.assert_not_called()

    async def test_dm_message_creates_conversation_and_appends_event(self):
        graph = FakeGraph(frames=[], snapshot=make_snapshot({"messages": []}))
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        channel = make_channel(discord.DMChannel, 42)
        message = make_message("hello there", channel=channel)

        await bot.TapestryDiscordClient.on_message(client, message)

        assert "discord-42" in client._known_conversations
        conn = bot.get_connection()
        row = conn.execute("SELECT kind FROM conversations WHERE id = ?", ("discord-42",)).fetchone()
        assert row[0] == "dm"

        logged = events_module.read_events("discord-42")
        user_messages = [e for e in logged if e.type == "user/message"]
        assert len(user_messages) == 1
        assert user_messages[0].actor == "human"
        assert user_messages[0].payload["text"] == "hello there"

        channel.send.assert_awaited_once()
        client.drive_graph.assert_awaited_once()
        call_args = client.drive_graph.await_args.args
        assert call_args[0] is channel
        assert call_args[1] == "discord-42"

    async def test_mentioned_message_strips_mention_and_resolves_persona(self):
        graph = FakeGraph(frames=[], snapshot=make_snapshot({"messages": []}))
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        channel = make_channel(discord.TextChannel, 7)
        bot_user = client._connection.user
        message = make_message("<@999> Rex can you look into this", channel=channel, mentions=[bot_user])

        await bot.TapestryDiscordClient.on_message(client, message)

        logged = events_module.read_events("discord-7")
        user_message = next(e for e in logged if e.type == "user/message")
        assert user_message.payload["text"] == "Rex can you look into this"

        state = client.drive_graph.await_args.args[2]
        assert state["persona_id"] == "rex"

    async def test_ignores_empty_message_after_stripping_mention(self):
        client = make_client()
        client.drive_graph = AsyncMock()
        channel = make_channel(discord.TextChannel, 7)
        bot_user = client._connection.user
        message = make_message("<@999>", channel=channel, mentions=[bot_user])

        await bot.TapestryDiscordClient.on_message(client, message)

        client.drive_graph.assert_not_called()

    async def test_thread_command_spins_off_a_new_thread(self):
        graph = FakeGraph(frames=[], snapshot=make_snapshot({"messages": []}))
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        parent_channel = make_channel(discord.TextChannel, 7)
        new_thread = make_channel(discord.Thread, 888, parent_id=7)
        bot_user = client._connection.user
        message = make_message(
            "<@999> /thread let's dig into this", channel=parent_channel, mentions=[bot_user]
        )
        message.create_thread = AsyncMock(return_value=new_thread)

        await bot.TapestryDiscordClient.on_message(client, message)

        message.create_thread.assert_awaited_once()
        # The conversation stays bound to the PARENT channel; only
        # state["thread_id"] reflects the new Discord thread.
        call_args = client.drive_graph.await_args.args
        assert call_args[1] == "discord-7"
        state = call_args[2]
        assert state["thread_id"] == "888"
        new_thread.send.assert_awaited_once()  # placeholder posted inside the thread


# ===========================================================================
# drive_graph -- streaming edits, final post, approval prompt
# ===========================================================================


class TestDriveGraph:
    async def test_streams_frames_onto_the_placeholder(self):
        frames = [
            {"type": "persona/thinking", "payload": {"persona_id": "rex"}},
            {"type": "tool/status", "payload": {"tool_name": "terminal", "status": "running"}},
        ]
        snapshot = make_snapshot({"persona_id": "rex", "messages": [
            {"role": "assistant", "content": "all done"},
        ]})
        graph = FakeGraph(frames=frames, snapshot=snapshot)
        client = make_client(graph)
        channel = make_channel(discord.TextChannel, 1)
        placeholder = MagicMock()
        placeholder.edit = AsyncMock()
        placeholder.delete = AsyncMock()

        await bot.TapestryDiscordClient.drive_graph(client, channel, "discord-1", {}, placeholder)

        assert placeholder.edit.await_count == 2
        edited_texts = [call.kwargs["content"] for call in placeholder.edit.await_args_list]
        assert edited_texts[0] == "_Rex is thinking…_"
        assert "terminal" in edited_texts[1]

    async def test_posts_final_reply_via_the_persona_webhook_when_finished(self, monkeypatch):
        snapshot = make_snapshot(
            {"persona_id": "rex", "messages": [{"role": "assistant", "content": "here you go"}]}
        )
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        channel = make_channel(discord.TextChannel, 1)
        placeholder = MagicMock()
        placeholder.delete = AsyncMock()

        post_as_persona = AsyncMock()
        monkeypatch.setattr(webhook_identity, "post_as_persona", post_as_persona)

        await bot.TapestryDiscordClient.drive_graph(client, channel, "discord-1", {}, placeholder)

        placeholder.delete.assert_awaited_once()
        post_as_persona.assert_awaited_once()
        args, kwargs = post_as_persona.await_args
        assert args[0] is channel
        assert args[1] is REX
        assert args[2] == "here you go"

    async def test_posts_into_the_parent_channel_when_driven_inside_a_thread(self, monkeypatch):
        snapshot = make_snapshot(
            {"persona_id": "rex", "messages": [{"role": "assistant", "content": "ok"}]}
        )
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        parent = make_channel(discord.TextChannel, 1)
        thread = make_channel(discord.Thread, 2, parent_id=1, parent=parent)
        placeholder = MagicMock()
        placeholder.delete = AsyncMock()

        post_as_persona = AsyncMock()
        monkeypatch.setattr(webhook_identity, "post_as_persona", post_as_persona)

        await bot.TapestryDiscordClient.drive_graph(client, thread, "discord-1", {}, placeholder)

        args, kwargs = post_as_persona.await_args
        assert args[0] is parent
        assert kwargs["thread"] is thread

    async def test_posts_approval_request_when_paused(self):
        snapshot = make_snapshot(
            {"persona_id": "rex", "messages": []},
            interrupts=(
                make_interrupt(
                    request_id="req-1", tool_name="terminal", arguments={"command": "ls"}
                ),
            ),
        )
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        channel = make_channel(discord.TextChannel, 1)
        placeholder = MagicMock()
        placeholder.delete = AsyncMock()

        await bot.TapestryDiscordClient.drive_graph(client, channel, "discord-1", {}, placeholder)

        placeholder.delete.assert_awaited_once()
        channel.send.assert_awaited_once()
        _, kwargs = channel.send.await_args
        assert "terminal" in kwargs["content"]
        assert "Rex" in kwargs["content"]
        view = kwargs["view"]
        assert isinstance(view, bot.ApproveReject)
        assert view.conversation_id == "discord-1"
        assert view.request_id == "req-1"

    async def test_a_graph_exception_edits_a_visible_error_instead_of_hanging_silently(self):
        # graph/build.py deliberately RAISES for real conditions
        # (TurnBudgetExceeded, DelegationDepthExceeded, ...) rather than
        # swallowing them -- drive_graph must never let that propagate
        # out into discord.py's own swallow-and-log handler and leave the
        # human staring at a "_...is thinking..._" placeholder forever.
        graph = FakeGraph(frames=[], snapshot=None, error=TurnBudgetExceeded("too many turns"))
        client = make_client(graph)
        channel = make_channel(discord.TextChannel, 1)
        placeholder = MagicMock()
        placeholder.edit = AsyncMock()
        placeholder.delete = AsyncMock()

        await bot.TapestryDiscordClient.drive_graph(client, channel, "discord-1", {}, placeholder)

        placeholder.edit.assert_awaited_once()
        assert "TurnBudgetExceeded" in placeholder.edit.await_args.kwargs["content"]
        # No approval prompt, no fallback plain-text post, no delete --
        # the failure short-circuits before any of that logic runs.
        channel.send.assert_not_called()
        placeholder.delete.assert_not_called()

    async def test_an_exception_mid_stream_after_some_frames_still_reports_visibly(self):
        frames = [{"type": "persona/thinking", "payload": {"persona_id": "rex"}}]
        graph = FakeGraph(frames=frames, snapshot=None, error=RuntimeError("boom"))
        client = make_client(graph)
        channel = make_channel(discord.TextChannel, 1)
        placeholder = MagicMock()
        placeholder.edit = AsyncMock()
        placeholder.delete = AsyncMock()

        await bot.TapestryDiscordClient.drive_graph(client, channel, "discord-1", {}, placeholder)

        # One edit for the "thinking" frame, one for the error.
        assert placeholder.edit.await_count == 2
        assert "RuntimeError" in placeholder.edit.await_args.kwargs["content"]


# ===========================================================================
# drive_graph against a REAL compiled graph -- not FakeGraph.
#
# Every test above proves drive_graph agrees with ITSELF (FakeGraph is a
# SimpleNamespace this file wrote). This proves drive_graph agrees with
# graph/build.py's ACTUAL, tested behavior: that
# astream(..., stream_mode="custom") really carries graph.streaming.emit(
# ...) frames end to end, that aget_state(...).interrupts really
# populates on a genuine interrupt() pause (not just on a hand-built
# SimpleNamespace), that build._decode_decision really accepts the bare
# bool this adapter passes via Command(resume=...), and that the resume
# path really reaches next_node == "end" and posts through the webhook.
# Mirrors tests/graph/test_build.py's own pattern: real AsyncSqliteSaver
# against a tmp_path file, only call_model (and, here, the tool
# implementation) mocked.
# ===========================================================================


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _plain_model_response(text: str):
    from tapestry.models.litellm_client import ModelResponse

    return ModelResponse(text=text, tool_calls=None)


def _tool_call_model_response(text: str, name: str, arguments: dict, call_id: str = "call_1"):
    from tapestry.models.litellm_client import ModelResponse

    return ModelResponse(text=text, tool_calls=[_tool_call(name, arguments, call_id)])


class TestDriveGraphAgainstARealGraph:
    async def test_pause_then_resume_end_to_end(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from tapestry.graph import build as graph_build
        from tapestry.tools.file_editor import ToolResult

        conversation_id = "conv-discord-integration-1"
        checkpoint_path = str(tmp_path / "checkpoint.sqlite")
        graph = await graph_build.build_graph(checkpoint_path)
        try:
            propose = _tool_call_model_response(
                "I'll edit the file.",
                "file_editor",
                {"command": "create", "path": "/tmp/x.txt", "file_text": "hi"},
            )
            final = _plain_model_response("All done, file created.")
            call_model_mock = AsyncMock(side_effect=[propose, final])

            async def fake_file_editor(arguments: dict) -> ToolResult:
                return ToolResult(text="wrote it", is_error=False)

            client = make_client(graph)
            channel = make_channel(discord.TextChannel, 1)
            placeholder1 = MagicMock()
            placeholder1.edit = AsyncMock()
            placeholder1.delete = AsyncMock()

            with patch.object(graph_build, "call_model", call_model_mock), patch.dict(
                graph_build.TOOL_REGISTRY, {"file_editor": fake_file_editor}
            ):
                state = graph_build.new_state(conversation_id, "rex")
                await bot.TapestryDiscordClient.drive_graph(
                    client, channel, conversation_id, state, placeholder1
                )

                # Paused: drive_graph read a REAL interrupts() pause off
                # aget_state() and posted a real ApproveReject prompt.
                channel.send.assert_awaited_once()
                _, kwargs = channel.send.await_args
                view = kwargs["view"]
                assert isinstance(view, bot.ApproveReject)
                assert view.conversation_id == conversation_id

                logged = events_module.read_events(conversation_id)
                ask_requested = next(e for e in logged if e.type == "ask/requested")
                assert ask_requested.payload["questions"][0]["id"] == view.request_id
                assert not any(e.type == "tool/result" for e in logged), (
                    "tool must not run while paused"
                )

                # Resume with an approval, through the exact same
                # drive_graph entry point handle_approval_click uses.
                placeholder2 = MagicMock()
                placeholder2.edit = AsyncMock()
                placeholder2.delete = AsyncMock()
                post_as_persona = AsyncMock()
                monkeypatch.setattr(webhook_identity, "post_as_persona", post_as_persona)

                await bot.TapestryDiscordClient.drive_graph(
                    client, channel, conversation_id, Command(resume=True), placeholder2
                )

                post_as_persona.assert_awaited_once()
                args, _ = post_as_persona.await_args
                assert args[1] is REX  # bot.PERSONAS fixture entry for "rex"
                assert args[2] == "All done, file created."

                logged_after = events_module.read_events(conversation_id)
                assert any(e.type == "tool/result" for e in logged_after), (
                    "tool must run exactly once, after resume"
                )
                ask_answered = next(e for e in logged_after if e.type == "ask/answered")
                assert ask_answered.payload["answers"][0]["selected"] == ["approve"]
        finally:
            await graph.checkpointer.conn.close()


# ===========================================================================
# handle_approval_click -- the resume-decoding logic behind the buttons
# ===========================================================================


def make_interaction(message_content: str = "orig content", user_name: str = "Ann") -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.content = message_content
    interaction.user = MagicMock()
    interaction.user.display_name = user_name
    interaction.channel = make_channel(discord.TextChannel, 1)
    return interaction


class TestHandleApprovalClick:
    async def test_approve_resumes_with_true(self):
        snapshot = make_snapshot({}, interrupts=(make_interrupt(request_id="req-1"),))
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        interaction = make_interaction()

        await bot.TapestryDiscordClient.handle_approval_click(
            client, interaction, "discord-1", "req-1", approved=True
        )

        interaction.response.edit_message.assert_awaited_once()
        assert "Approved" in interaction.response.edit_message.await_args.kwargs["content"]
        client.drive_graph.assert_awaited_once()
        graph_input = client.drive_graph.await_args.args[2]
        assert isinstance(graph_input, Command)
        assert graph_input.resume is True

    async def test_reject_resumes_with_false(self):
        snapshot = make_snapshot({}, interrupts=(make_interrupt(request_id="req-1"),))
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        interaction = make_interaction()

        await bot.TapestryDiscordClient.handle_approval_click(
            client, interaction, "discord-1", "req-1", approved=False
        )

        assert "Rejected" in interaction.response.edit_message.await_args.kwargs["content"]
        graph_input = client.drive_graph.await_args.args[2]
        assert isinstance(graph_input, Command)
        assert graph_input.resume is False

    async def test_stale_click_when_no_interrupt_pending(self):
        snapshot = make_snapshot({}, interrupts=())
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        interaction = make_interaction()

        await bot.TapestryDiscordClient.handle_approval_click(
            client, interaction, "discord-1", "req-1", approved=True
        )

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        client.drive_graph.assert_not_called()
        interaction.response.edit_message.assert_not_called()

    async def test_stale_click_when_pending_interrupt_is_a_different_request(self):
        snapshot = make_snapshot({}, interrupts=(make_interrupt(request_id="req-OTHER"),))
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        interaction = make_interaction()

        await bot.TapestryDiscordClient.handle_approval_click(
            client, interaction, "discord-1", "req-1", approved=True
        )

        interaction.response.send_message.assert_awaited_once()
        client.drive_graph.assert_not_called()

    async def test_dedupes_concurrent_clicks_on_the_same_request(self):
        graph = FakeGraph(frames=[], snapshot=make_snapshot({}))
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        client._in_flight_resumes.add(("discord-1", "req-1"))
        interaction = make_interaction()

        await bot.TapestryDiscordClient.handle_approval_click(
            client, interaction, "discord-1", "req-1", approved=True
        )

        interaction.response.send_message.assert_awaited_once()
        assert "Already processing" in interaction.response.send_message.await_args.args[0]
        client.drive_graph.assert_not_called()
        # aget_state should never even be consulted -- the dedupe check
        # happens first.
        assert graph.aget_state_calls == []

    async def test_in_flight_key_is_cleared_after_completion(self):
        snapshot = make_snapshot({}, interrupts=(make_interrupt(request_id="req-1"),))
        graph = FakeGraph(frames=[], snapshot=snapshot)
        client = make_client(graph)
        client.drive_graph = AsyncMock()
        interaction = make_interaction()

        await bot.TapestryDiscordClient.handle_approval_click(
            client, interaction, "discord-1", "req-1", approved=True
        )

        assert ("discord-1", "req-1") not in client._in_flight_resumes


# ===========================================================================
# ApproveReject view -- button click wiring end to end
# ===========================================================================


class TestApproveRejectView:
    def test_buttons_get_distinct_custom_ids(self):
        client = make_client()
        view = bot.ApproveReject(client, "discord-1", "req-1")
        assert view.approve.custom_id == "tapestry:approve:discord-1:req-1"
        assert view.reject.custom_id == "tapestry:reject:discord-1:req-1"

    def test_view_is_persistent(self):
        client = make_client()
        view = bot.ApproveReject(client, "discord-1", "req-1")
        assert view.is_persistent() is True

    async def test_clicking_approve_calls_client_handle_approval_click(self):
        client = make_client()
        client.handle_approval_click = AsyncMock()
        view = bot.ApproveReject(client, "discord-1", "req-1")
        interaction = make_interaction()

        await view.approve.callback(interaction)

        client.handle_approval_click.assert_awaited_once_with(
            interaction, "discord-1", "req-1", approved=True
        )

    async def test_clicking_reject_calls_client_handle_approval_click(self):
        client = make_client()
        client.handle_approval_click = AsyncMock()
        view = bot.ApproveReject(client, "discord-1", "req-1")
        interaction = make_interaction()

        await view.reject.callback(interaction)

        client.handle_approval_click.assert_awaited_once_with(
            interaction, "discord-1", "req-1", approved=False
        )


# ===========================================================================
# setup_hook -- persistent-view recovery across a restart
# ===========================================================================


class TestSetupHookRecovery:
    async def test_registers_a_view_for_every_pending_approval(self):
        bot.ensure_conversation_row("discord-1", "group")
        bot.ensure_conversation_row("discord-2", "dm")
        events_module.append_event(
            "discord-1",
            "ask/requested",
            actor="system",
            payload={"questions": [{"id": "req-1", "intent": "approval"}]},
        )
        events_module.append_event(
            "discord-2",
            "ask/requested",
            actor="system",
            payload={"questions": [{"id": "req-2", "intent": "approval"}]},
        )
        # req-2 already answered -- should NOT get a view.
        events_module.append_event(
            "discord-2", "ask/answered", actor="human", payload={"request_id": "req-2", "answers": []}
        )
        client = make_client()

        await bot.TapestryDiscordClient.setup_hook(client)

        assert client.add_view.call_count == 1
        registered_view = client.add_view.call_args.args[0]
        assert isinstance(registered_view, bot.ApproveReject)
        assert registered_view.conversation_id == "discord-1"
        assert registered_view.request_id == "req-1"

    async def test_no_pending_approvals_registers_nothing(self):
        bot.ensure_conversation_row("discord-1", "group")
        client = make_client()

        await bot.TapestryDiscordClient.setup_hook(client)

        client.add_view.assert_not_called()


# ===========================================================================
# ensure_conversation -- close_orphaned_turns called once per conversation
# ===========================================================================


class TestEnsureConversation:
    async def test_closes_orphaned_turns_the_first_time(self, monkeypatch):
        close_orphaned = MagicMock(wraps=events_module.close_orphaned_turns)
        monkeypatch.setattr(events_module, "close_orphaned_turns", close_orphaned)
        client = make_client()

        await bot.TapestryDiscordClient.ensure_conversation(client, "discord-1", is_dm=False)

        close_orphaned.assert_called_once_with("discord-1")
        assert "discord-1" in client._known_conversations

    async def test_does_not_repeat_for_an_already_known_conversation(self, monkeypatch):
        close_orphaned = MagicMock(wraps=events_module.close_orphaned_turns)
        monkeypatch.setattr(events_module, "close_orphaned_turns", close_orphaned)
        client = make_client()
        client._known_conversations.add("discord-1")

        await bot.TapestryDiscordClient.ensure_conversation(client, "discord-1", is_dm=False)

        close_orphaned.assert_not_called()

    async def test_records_dm_kind_correctly(self):
        client = make_client()
        await bot.TapestryDiscordClient.ensure_conversation(client, "discord-9", is_dm=True)
        conn = bot.get_connection()
        row = conn.execute("SELECT kind FROM conversations WHERE id = ?", ("discord-9",)).fetchone()
        assert row[0] == "dm"
