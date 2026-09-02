"""Tests for the Telegram adapter (`bot.py` / `identity.py`).

python-telegram-bot's real `Bot`/`Application`/`Update`/`CallbackQuery`
classes are never constructed for real (no polling, no HTTP calls to
Telegram) -- every PTB-shaped object used here is a lightweight duck-typed
`SimpleNamespace` (`context.bot`, `update.effective_message`, `query.
message`, ...) plus `AsyncMock`/`MagicMock` for the handful of bound
methods the adapter actually calls (`send_message`, `edit_text`, `delete`,
`answer`, `edit_message_text`). `TelegramAdapter` itself needs no
`__new__`-bypass trick the way `TapestryDiscordClient` does -- it doesn't
subclass anything from the library, so a plain constructor call is enough
to exercise its REAL bound methods (`on_message`, `on_callback_query`,
`drive_graph`, ...) against a `FakeGraph` stand-in for
`CompiledStateGraph`.

`core.events`/the `conversations` table are exercised for real against an
isolated in-memory SQLite connection (see `conftest.py` in this package)
-- covers "the message -> event append flow" against the genuine
event-log mechanics, not a mock of `append_event`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Command

from tapestry.adapters.telegram_adapter import bot, identity
from tapestry.core import events as events_module
from tapestry.core.personas import Persona
from tapestry.graph.budgets import TurnBudgetExceeded

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
PERSONAS_FIXTURE = {"ada": ADA, "rex": REX}


class FakeGraph:
    """Duck-typed stand-in for `CompiledStateGraph` -- records every
    `astream`/`aget_state` call and replays scripted responses.

    `snapshots` is consumed in order across successive `aget_state` calls
    (mirroring the real checkpointer's state actually changing between a
    pre-flight check and a post-run read); once exhausted, the last value
    repeats, so a test that doesn't care about the two-step sequence can
    just pass one.
    """

    def __init__(self, frames: list[dict], snapshots: list[object]) -> None:
        self._frames = frames
        self._snapshots = list(snapshots)
        self.astream_calls: list[tuple] = []
        self.aget_state_calls: list[dict] = []

    def astream(self, graph_input, config, stream_mode):
        self.astream_calls.append((graph_input, config, stream_mode))

        async def _generator():
            for frame in self._frames:
                yield frame

        return _generator()

    async def aget_state(self, config):
        self.aget_state_calls.append(config)
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]


def make_snapshot(values: dict, interrupts: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(values=values, interrupts=interrupts)


def make_interrupt(**value: object) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def make_adapter(graph: object | None = None, personas: dict | None = None) -> bot.TelegramAdapter:
    return bot.TelegramAdapter(graph, personas=personas if personas is not None else PERSONAS_FIXTURE)


def make_chat(chat_id: int, chat_type: str = "private", *, title: str | None = None, is_forum: bool = False):
    return SimpleNamespace(id=chat_id, type=chat_type, title=title, is_forum=is_forum)


def make_message(text: str, *, chat, message_thread_id: int | None = None):
    return SimpleNamespace(text=text, chat=chat, message_thread_id=message_thread_id)


def make_update(*, message=None, callback_query=None, user_name: str = "A Human"):
    effective_chat = message.chat if message is not None else (
        callback_query.message.chat if callback_query is not None else None
    )
    return SimpleNamespace(
        effective_message=message,
        effective_chat=effective_chat,
        effective_user=SimpleNamespace(full_name=user_name),
        callback_query=callback_query,
    )


def make_placeholder(message_id: int = 1):
    placeholder = SimpleNamespace()
    placeholder.message_id = message_id
    placeholder.edit_text = AsyncMock()
    placeholder.delete = AsyncMock()
    return placeholder


def make_context(bot_username: str = "tapestry_bot", send_result=None):
    fake_bot = SimpleNamespace(
        username=bot_username,
        send_message=AsyncMock(return_value=send_result or make_placeholder()),
    )
    return SimpleNamespace(bot=fake_bot)


def make_callback_query(data: str, *, chat, text: str = "orig content", message_thread_id=None):
    message = SimpleNamespace(chat=chat, text=text, message_thread_id=message_thread_id)
    query = SimpleNamespace(
        data=data,
        message=message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return query


# ===========================================================================
# Pure helpers: conversation_id / kind / thread_id mapping
# ===========================================================================


class TestConversationIdMapping:
    def test_maps_chat_id_to_telegram_prefixed_id(self):
        assert bot.conversation_id_for_chat(111) == "telegram-111"

    def test_negative_supergroup_chat_id_round_trips(self):
        assert bot.conversation_id_for_chat(-1001234567890) == "telegram--1001234567890"


class TestConversationKind:
    def test_private_chat_is_dm(self):
        assert bot.conversation_kind("private") == "dm"

    def test_group_chat_is_group(self):
        assert bot.conversation_kind("group") == "group"

    def test_supergroup_chat_is_group(self):
        assert bot.conversation_kind("supergroup") == "group"


class TestForumThreadId:
    def test_none_outside_a_forum(self):
        assert bot.forum_thread_id(False, 42) is None

    def test_passes_through_inside_a_forum(self):
        assert bot.forum_thread_id(True, 42) == 42

    def test_none_when_forum_but_no_topic_on_the_message(self):
        assert bot.forum_thread_id(True, None) is None


# ===========================================================================
# Pure helpers: persona addressing
# ===========================================================================


class TestMatchNamedPersona:
    def test_matches_persona_name_as_first_word(self):
        assert bot.match_named_persona(PERSONAS_FIXTURE, "Rex can you look into this") is REX

    def test_case_insensitive(self):
        assert bot.match_named_persona(PERSONAS_FIXTURE, "rex, please help") is REX

    def test_strips_punctuation_before_matching(self):
        assert bot.match_named_persona(PERSONAS_FIXTURE, "Rex, can you help?") is REX

    def test_at_prefixed_name_also_matches(self):
        # "@" is in string.punctuation, so it's stripped the same way a
        # trailing comma is -- "@Rex do X" and "Rex, do X" both resolve.
        assert bot.match_named_persona(PERSONAS_FIXTURE, "@Rex do X") is REX

    def test_none_when_no_persona_named(self):
        assert bot.match_named_persona(PERSONAS_FIXTURE, "hey can someone look at this") is None

    def test_none_on_empty_message(self):
        assert bot.match_named_persona(PERSONAS_FIXTURE, "   ") is None


class TestIsAddressed:
    def test_true_when_a_persona_is_named(self):
        assert bot.is_addressed(PERSONAS_FIXTURE, "Rex, do X", "tapestry_bot") is True

    def test_true_when_bot_username_is_mentioned(self):
        assert bot.is_addressed(PERSONAS_FIXTURE, "@tapestry_bot help please", "tapestry_bot") is True

    def test_bot_username_match_is_case_insensitive(self):
        assert bot.is_addressed(PERSONAS_FIXTURE, "@Tapestry_Bot help", "tapestry_bot") is True

    def test_false_for_unaddressed_chatter(self):
        assert bot.is_addressed(PERSONAS_FIXTURE, "hey everyone, lunch?", "tapestry_bot") is False

    def test_false_when_bot_username_unknown_and_no_persona_named(self):
        assert bot.is_addressed(PERSONAS_FIXTURE, "hello", None) is False


# ===========================================================================
# Pure helpers: render_stream_frame / last_assistant_text / decode_callback_data
# ===========================================================================


class TestRenderStreamFrame:
    def test_persona_thinking_renders_persona_name(self):
        frame = {"type": "persona/thinking", "payload": {"persona_id": "rex"}}
        assert bot.render_stream_frame(PERSONAS_FIXTURE, frame) == "\U0001f4ad Rex is thinking…"

    def test_persona_thinking_falls_back_to_raw_id_for_unknown_persona(self):
        frame = {"type": "persona/thinking", "payload": {"persona_id": "ghost"}}
        assert "ghost" in bot.render_stream_frame(PERSONAS_FIXTURE, frame)

    def test_tool_status_running(self):
        frame = {"type": "tool/status", "payload": {"tool_name": "terminal", "status": "running"}}
        text = bot.render_stream_frame(PERSONAS_FIXTURE, frame)
        assert "terminal" in text
        assert "running" in text

    def test_tool_status_done_success(self):
        frame = {
            "type": "tool/status",
            "payload": {"tool_name": "terminal", "status": "done", "is_error": False},
        }
        assert "done" in bot.render_stream_frame(PERSONAS_FIXTURE, frame)

    def test_tool_status_done_error(self):
        frame = {
            "type": "tool/status",
            "payload": {"tool_name": "terminal", "status": "done", "is_error": True},
        }
        assert "failed" in bot.render_stream_frame(PERSONAS_FIXTURE, frame)

    def test_unknown_frame_type_renders_nothing(self):
        frame = {"type": "persona/responded", "payload": {}}
        assert bot.render_stream_frame(PERSONAS_FIXTURE, frame) is None


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


class TestDecodeCallbackData:
    def test_round_trips_approve(self):
        assert bot.decode_callback_data("approve:req-1") == ("approve", "req-1")

    def test_round_trips_reject(self):
        assert bot.decode_callback_data("reject:req-1") == ("reject", "req-1")

    def test_rejects_unknown_action(self):
        assert bot.decode_callback_data("delete:req-1") is None

    def test_rejects_missing_separator(self):
        assert bot.decode_callback_data("approve") is None

    def test_rejects_empty_request_id(self):
        assert bot.decode_callback_data("approve:") is None


# ===========================================================================
# identity.format_persona_message
# ===========================================================================


class TestFormatPersonaMessage:
    def test_bolds_the_name_html_style(self):
        result = identity.format_persona_message(REX, "hello")
        assert result == "<b>Rex</b>\nhello"

    def test_escapes_html_special_characters_in_both_name_and_text(self):
        persona = _persona("x", "A & B")
        result = identity.format_persona_message(persona, "1 < 2 && 3 > 2")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result
        # No raw '<'/'>' left unescaped anywhere in the payload.
        assert "1 < 2" not in result

    def test_exports_the_parse_mode_used(self):
        assert identity.TELEGRAM_PARSE_MODE == "HTML"


# ===========================================================================
# ensure_conversation_row / TelegramAdapter.ensure_conversation
# ===========================================================================


class TestEnsureConversationRow:
    def test_creates_a_row(self):
        bot.ensure_conversation_row("telegram-1", "group")
        conn = bot.get_connection()
        row = conn.execute(
            "SELECT id, kind FROM conversations WHERE id = ?", ("telegram-1",)
        ).fetchone()
        assert row[0] == "telegram-1"
        assert row[1] == "group"

    def test_is_idempotent(self):
        bot.ensure_conversation_row("telegram-1", "group")
        bot.ensure_conversation_row("telegram-1", "group")  # must not raise
        conn = bot.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE id = ?", ("telegram-1",)
        ).fetchone()[0]
        assert count == 1


class TestEnsureConversation:
    async def test_creates_row_and_closes_orphaned_turns_once(self, monkeypatch):
        adapter = make_adapter()
        close_calls = []
        monkeypatch.setattr(
            events_module, "close_orphaned_turns", lambda cid: close_calls.append(cid)
        )

        await adapter.ensure_conversation("telegram-9", kind="dm", name="A Human")
        await adapter.ensure_conversation("telegram-9", kind="dm", name="A Human")

        assert close_calls == ["telegram-9"]
        conn = bot.get_connection()
        row = conn.execute(
            "SELECT kind, name FROM conversations WHERE id = ?", ("telegram-9",)
        ).fetchone()
        assert row[0] == "dm"
        assert row[1] == "A Human"


# ===========================================================================
# on_message -- routing + the message -> event append flow
# ===========================================================================


class TestOnMessage:
    async def test_ignores_group_message_not_addressed_to_anyone(self):
        adapter = make_adapter(FakeGraph(frames=[], snapshots=[make_snapshot({"messages": []})]))
        adapter.drive_graph = AsyncMock()
        chat = make_chat(1, "group")
        message = make_message("hey everyone, lunch?", chat=chat)
        update = make_update(message=message)
        context = make_context()

        await adapter.on_message(update, context)

        adapter.drive_graph.assert_not_called()
        context.bot.send_message.assert_not_called()

    async def test_dm_message_creates_conversation_and_appends_event(self):
        graph = FakeGraph(frames=[], snapshots=[make_snapshot({"messages": []})])
        adapter = make_adapter(graph)
        adapter.drive_graph = AsyncMock()
        chat = make_chat(42, "private")
        message = make_message("hello there", chat=chat)
        update = make_update(message=message)
        context = make_context()

        await adapter.on_message(update, context)

        assert "telegram-42" in adapter._known_conversations
        conn = bot.get_connection()
        row = conn.execute(
            "SELECT kind FROM conversations WHERE id = ?", ("telegram-42",)
        ).fetchone()
        assert row[0] == "dm"

        logged = events_module.read_events("telegram-42")
        user_messages = [e for e in logged if e.type == "user/message"]
        assert len(user_messages) == 1
        assert user_messages[0].actor == "human"
        assert user_messages[0].payload["text"] == "hello there"

        context.bot.send_message.assert_awaited_once()
        adapter.drive_graph.assert_awaited_once()
        call_args = adapter.drive_graph.await_args.args
        # (bot, chat_id, thread_id, conversation_id, state, placeholder)
        assert call_args[1] == 42
        assert call_args[3] == "telegram-42"
        state = call_args[4]
        assert state["persona_id"] == "ada"  # default persona in a DM

    async def test_group_message_addressed_by_persona_name_resolves_that_persona(self):
        graph = FakeGraph(frames=[], snapshots=[make_snapshot({"messages": []})])
        adapter = make_adapter(graph)
        adapter.drive_graph = AsyncMock()
        chat = make_chat(7, "supergroup")
        message = make_message("Rex can you look into this", chat=chat)
        update = make_update(message=message)
        context = make_context()

        await adapter.on_message(update, context)

        logged = events_module.read_events("telegram-7")
        user_message = next(e for e in logged if e.type == "user/message")
        # Unlike a stripped-mention design, the full text is kept verbatim
        # (matching discord_adapter's own resolve-but-don't-strip behavior).
        assert user_message.payload["text"] == "Rex can you look into this"

        state = adapter.drive_graph.await_args.args[4]
        assert state["persona_id"] == "rex"

    async def test_forum_topic_message_carries_thread_id(self):
        graph = FakeGraph(frames=[], snapshots=[make_snapshot({"messages": []})])
        adapter = make_adapter(graph)
        adapter.drive_graph = AsyncMock()
        chat = make_chat(7, "supergroup", is_forum=True)
        message = make_message("Rex, status update?", chat=chat, message_thread_id=55)
        update = make_update(message=message)
        context = make_context()

        await adapter.on_message(update, context)

        logged = events_module.read_events("telegram-7")
        user_message = next(e for e in logged if e.type == "user/message")
        # core.conversations.Message.thread_id / TapestryGraphState
        # ["thread_id"] are both typed `str | None` -- Telegram's raw int
        # message_thread_id is stringified at that boundary.
        assert user_message.payload["thread_id"] == "55"

        state = adapter.drive_graph.await_args.args[4]
        assert state["thread_id"] == "55"
        # drive_graph itself still gets the RAW int -- every Telegram API
        # call (message_thread_id=...) needs the real int, not the
        # stringified core-facing value.
        assert adapter.drive_graph.await_args.args[2] == 55

    async def test_pending_approval_blocks_a_new_message(self):
        paused_snapshot = make_snapshot(
            {"messages": []}, interrupts=(make_interrupt(request_id="req-1"),)
        )
        graph = FakeGraph(frames=[], snapshots=[paused_snapshot])
        adapter = make_adapter(graph)
        adapter.drive_graph = AsyncMock()
        chat = make_chat(42, "private")
        message = make_message("go ahead and do more", chat=chat)
        update = make_update(message=message)
        context = make_context()

        await adapter.on_message(update, context)

        adapter.drive_graph.assert_not_called()
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.await_args
        assert "pending" in kwargs["text"].lower()
        # No new user/message event -- the turn never actually started.
        logged = events_module.read_events("telegram-42")
        assert not any(e.type == "user/message" for e in logged)


# ===========================================================================
# drive_graph -- streaming edits, final reply, approval prompt, error handling
# ===========================================================================


class TestDriveGraph:
    async def test_streams_frames_onto_the_placeholder(self):
        frames = [
            {"type": "persona/thinking", "payload": {"persona_id": "rex"}},
            {"type": "tool/status", "payload": {"tool_name": "terminal", "status": "running"}},
        ]
        snapshot = make_snapshot(
            {"persona_id": "rex", "messages": [{"role": "assistant", "content": "all done"}]}
        )
        graph = FakeGraph(frames=frames, snapshots=[snapshot])
        adapter = make_adapter(graph)
        context = make_context()
        placeholder = make_placeholder()

        await adapter.drive_graph(context.bot, 1, None, "telegram-1", {}, placeholder)

        assert placeholder.edit_text.await_count == 2
        edited_texts = [call.kwargs["text"] for call in placeholder.edit_text.await_args_list]
        assert "Rex is thinking" in edited_texts[0]
        assert "terminal" in edited_texts[1]

    async def test_posts_final_reply_via_send_message_when_finished(self):
        snapshot = make_snapshot(
            {"persona_id": "rex", "messages": [{"role": "assistant", "content": "here you go"}]}
        )
        graph = FakeGraph(frames=[], snapshots=[snapshot])
        adapter = make_adapter(graph)
        context = make_context()
        placeholder = make_placeholder()

        await adapter.drive_graph(context.bot, 1, None, "telegram-1", {}, placeholder)

        placeholder.delete.assert_awaited_once()
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.await_args
        assert kwargs["chat_id"] == 1
        assert kwargs["text"] == "<b>Rex</b>\nhere you go"
        assert kwargs["parse_mode"] == identity.TELEGRAM_PARSE_MODE

    async def test_posts_approval_request_when_paused(self):
        snapshot = make_snapshot(
            {"persona_id": "rex", "messages": []},
            interrupts=(
                make_interrupt(request_id="req-1", tool_name="terminal", arguments={"command": "ls"}),
            ),
        )
        graph = FakeGraph(frames=[], snapshots=[snapshot])
        adapter = make_adapter(graph)
        context = make_context()
        placeholder = make_placeholder()

        await adapter.drive_graph(context.bot, 1, None, "telegram-1", {}, placeholder)

        placeholder.delete.assert_awaited_once()
        context.bot.send_message.assert_awaited_once()
        _, kwargs = context.bot.send_message.await_args
        assert "terminal" in kwargs["text"]
        assert "Rex" in kwargs["text"]
        keyboard = kwargs["reply_markup"]
        buttons = keyboard.inline_keyboard[0]
        assert buttons[0].callback_data == "approve:req-1"
        assert buttons[1].callback_data == "reject:req-1"

    async def test_budget_exceeded_reports_failure_without_crashing(self):
        class RaisingGraph(FakeGraph):
            def astream(self, graph_input, config, stream_mode):
                async def _generator():
                    raise TurnBudgetExceeded("turn budget exceeded: 10 >= 10")
                    yield  # pragma: no cover -- unreachable, makes this an async generator

                return _generator()

        graph = RaisingGraph(frames=[], snapshots=[make_snapshot({"messages": []})])
        adapter = make_adapter(graph)
        context = make_context()
        placeholder = make_placeholder()

        await adapter.drive_graph(context.bot, 1, None, "telegram-1", {}, placeholder)

        placeholder.edit_text.assert_awaited_once()
        _, kwargs = placeholder.edit_text.await_args
        assert "Stopped" in kwargs["text"]
        # The run never reached the final-reply step.
        context.bot.send_message.assert_not_called()
        assert graph.aget_state_calls == []


# ===========================================================================
# on_callback_query -- the inline-keyboard resume-decoding logic
# ===========================================================================


class TestOnCallbackQuery:
    async def test_approve_resumes_with_true_and_validates_against_the_live_interrupt(self):
        paused = make_snapshot({}, interrupts=(make_interrupt(request_id="req-1"),))
        finished = make_snapshot(
            {"persona_id": "rex", "messages": [{"role": "assistant", "content": "done!"}]}
        )
        graph = FakeGraph(frames=[], snapshots=[paused, finished])
        adapter = make_adapter(graph)
        chat = make_chat(9, "private")
        query = make_callback_query("approve:req-1", chat=chat)
        update = make_update(callback_query=query, user_name="Ann")
        context = make_context()

        await adapter.on_callback_query(update, context)

        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        _, kwargs = query.edit_message_text.await_args
        assert "APPROVED" in kwargs["text"]
        assert "Ann" in kwargs["text"]

        # The graph was actually resumed with Command(resume=True).
        assert len(graph.astream_calls) == 1
        graph_input, config, _ = graph.astream_calls[0]
        assert isinstance(graph_input, Command)
        assert graph_input.resume is True
        assert config["configurable"]["thread_id"] == "telegram-9"

        # Final reply posted after resuming.
        context.bot.send_message.assert_awaited()
        final_call = context.bot.send_message.await_args_list[-1]
        assert "done!" in final_call.kwargs["text"]

    async def test_reject_resumes_with_false(self):
        paused = make_snapshot({}, interrupts=(make_interrupt(request_id="req-1"),))
        finished = make_snapshot({"persona_id": "rex", "messages": []})
        graph = FakeGraph(frames=[], snapshots=[paused, finished])
        adapter = make_adapter(graph)
        chat = make_chat(9, "private")
        query = make_callback_query("reject:req-1", chat=chat)
        update = make_update(callback_query=query)
        context = make_context()

        await adapter.on_callback_query(update, context)

        graph_input, _, _ = graph.astream_calls[0]
        assert graph_input.resume is False
        _, kwargs = query.edit_message_text.await_args
        assert "REJECTED" in kwargs["text"]

    async def test_stale_request_id_is_rejected_without_resuming(self):
        # The graph has already moved on (or was never paused on THIS
        # request) -- current interrupt (if any) doesn't match the click.
        current = make_snapshot({}, interrupts=(make_interrupt(request_id="req-OTHER"),))
        graph = FakeGraph(frames=[], snapshots=[current])
        adapter = make_adapter(graph)
        chat = make_chat(9, "private")
        query = make_callback_query("approve:req-1", chat=chat)
        update = make_update(callback_query=query)
        context = make_context()

        await adapter.on_callback_query(update, context)

        assert graph.astream_calls == []  # never resumed
        query.edit_message_text.assert_awaited_once()
        _, kwargs = query.edit_message_text.await_args
        assert "already" in kwargs["text"].lower() or "unknown" in kwargs["text"].lower()

    async def test_no_pending_interrupt_at_all_is_rejected_without_resuming(self):
        current = make_snapshot({}, interrupts=())
        graph = FakeGraph(frames=[], snapshots=[current])
        adapter = make_adapter(graph)
        chat = make_chat(9, "private")
        query = make_callback_query("approve:req-1", chat=chat)
        update = make_update(callback_query=query)
        context = make_context()

        await adapter.on_callback_query(update, context)

        assert graph.astream_calls == []

    @pytest.mark.parametrize("data", ["not-ours:1", "approve", "approve:", "delete:req-1"])
    async def test_malformed_or_foreign_callback_data_is_ignored(self, data):
        graph = FakeGraph(frames=[], snapshots=[make_snapshot({})])
        adapter = make_adapter(graph)
        chat = make_chat(9, "private")
        query = make_callback_query(data, chat=chat)
        update = make_update(callback_query=query)
        context = make_context()

        await adapter.on_callback_query(update, context)

        query.answer.assert_awaited_once()  # still ack'd, per the Bot API requirement
        query.edit_message_text.assert_not_called()
        assert graph.aget_state_calls == []


# ===========================================================================
# build_application -- the one thing that constructs a REAL PTB Application
# ===========================================================================


# A syntactically valid-shaped (but fake) bot token -- `Application.builder()
# .token(...).build()` only validates shape locally, it never calls
# Telegram's servers (that only happens on `initialize()`/polling), so this
# is safe to construct in a test with no network access.
_DUMMY_TOKEN = "123456:AAF-dummy-token-for-testing-only"


class TestBuildApplication:
    def test_registers_a_message_handler_and_a_callback_query_handler(self):
        from telegram.ext import CallbackQueryHandler, MessageHandler

        adapter = make_adapter(FakeGraph(frames=[], snapshots=[make_snapshot({})]))
        application = adapter.build_application(_DUMMY_TOKEN)

        handlers = application.handlers[0]
        message_handlers = [h for h in handlers if isinstance(h, MessageHandler)]
        callback_handlers = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
        assert len(message_handlers) == 1
        assert len(callback_handlers) == 1
        assert message_handlers[0].callback == adapter.on_message
        assert callback_handlers[0].callback == adapter.on_callback_query

    def test_callback_query_handler_pattern_matches_real_emitted_callback_data(self):
        adapter = make_adapter(FakeGraph(frames=[], snapshots=[make_snapshot({})]))
        application = adapter.build_application(_DUMMY_TOKEN)

        from telegram.ext import CallbackQueryHandler

        handler = next(
            h for h in application.handlers[0] if isinstance(h, CallbackQueryHandler)
        )
        # These are the EXACT strings `_post_approval_request` builds
        # (`f"{_APPROVE}:{request_id}"` / `f"{_REJECT}:{request_id}"`) --
        # if the handler's `pattern` and that format ever drift apart, a
        # real button click would be silently dropped with no test to
        # catch it.
        assert handler.pattern.match("approve:07d3f1e2c9")
        assert handler.pattern.match("reject:07d3f1e2c9")
        assert handler.pattern.match("delete:anything") is None
