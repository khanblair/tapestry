"""Tests for tapestry.skills.catalog_sync — the content-hash-then-replace
catalog-coherence technique (see catalog_sync.py's own module docstring and
docs/vendor-research/ANALYSIS-deepseek-harness.md §3).

`catalog_sync.py` imports `from tapestry.core.events import append_event,
read_events` at module scope, against the shared cross-team contract. At the
time this test file was WRITTEN, `core/events.py` did not exist yet (a
sibling agent was building it in parallel); by the time it actually runs,
that module exists for real (a plain SQLite-backed `TapestryEvent` log, see
`tapestry/core/events.py`). This file handles both cases:

- If `tapestry.core.events` is importable, it's used as-is — exercising
  catalog_sync.py against the real event log — with storage redirected to a
  fresh in-memory SQLite connection per test via the same monkeypatch
  technique `tests/core/conftest.py` already established for the sibling
  `core` test suite (`get_connection` swapped out), so this file stays
  hermetic (no disk writes, no state shared across tests) either way.
- If it genuinely isn't importable (e.g. this file runs before
  `core/events.py` lands), a small in-memory FAKE module implementing just
  the two functions catalog_sync.py depends on is installed into
  `sys.modules` instead, matching the given contract
  (`append_event(conversation_id, type, actor, payload) -> event`, assumed
  `read_events(conversation_id) -> list[event]` in append order).
"""

from __future__ import annotations

import sqlite3
import sys
import types
import uuid
from typing import Any

import pytest

from tapestry.skills.registry import SkillSummary


def _install_fake_core_events_if_missing() -> types.ModuleType:
    try:
        import tapestry.core.events as real_events

        return real_events
    except ModuleNotFoundError:
        pass

    fake_module = types.ModuleType("tapestry.core.events")
    store: dict[str, list[Any]] = {}

    class _FakeEvent:
        def __init__(self, conversation_id: str, type: str, actor: str, payload: dict):
            self.conversation_id = conversation_id
            self.type = type
            self.actor = actor
            self.payload = payload

    def append_event(conversation_id: str, type: str, actor: str, payload: dict) -> _FakeEvent:
        event = _FakeEvent(conversation_id, type, actor, payload)
        store.setdefault(conversation_id, []).append(event)
        return event

    def read_events(conversation_id: str) -> list[_FakeEvent]:
        return list(store.get(conversation_id, []))

    fake_module.append_event = append_event  # type: ignore[attr-defined]
    fake_module.read_events = read_events  # type: ignore[attr-defined]
    sys.modules["tapestry.core.events"] = fake_module
    return fake_module


_install_fake_core_events_if_missing()

import tapestry.core.events as core_events  # noqa: E402  (after fake install, above)
from tapestry.skills.catalog_sync import (  # noqa: E402
    EVENT_TYPE,
    digest_catalog,
    sync_catalog,
)


@pytest.fixture(autouse=True)
def isolated_events_storage(monkeypatch: pytest.MonkeyPatch):
    """When the REAL tapestry.core.events module is in use, redirect its
    storage to a fresh in-memory SQLite connection per test -- the fake
    fallback module is already dict-backed and needs no such redirect (it
    also has no `get_connection` attribute to patch).
    """
    if not hasattr(core_events, "get_connection"):
        yield
        return

    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(core_events, "get_connection", lambda: conn)
    yield
    conn.close()


def _summary(
    name: str, description: str, *, rank: int = 100, source: str = "/fake/source"
) -> SkillSummary:
    return SkillSummary(
        name=name,
        description=description,
        when_to_use=None,
        user_invocable=True,
        rank=rank,
        source=source,
    )


class _StubRegistry:
    """Duck-types SkillRegistry's only method catalog_sync.py calls."""

    def __init__(self, summaries: list[SkillSummary]) -> None:
        self.summaries = summaries

    def discover(self) -> list[SkillSummary]:
        return list(self.summaries)


def _new_conversation_id() -> str:
    return f"conv-{uuid.uuid4()}"


# --- digest_catalog ----------------------------------------------------------


def test_digest_is_a_sha256_hex_string() -> None:
    digest = digest_catalog([_summary("alpha", "Alpha desc")])

    assert isinstance(digest, str)
    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex


def test_digest_is_order_independent() -> None:
    a = _summary("alpha", "Alpha desc")
    b = _summary("beta", "Beta desc")

    assert digest_catalog([a, b]) == digest_catalog([b, a])


def test_digest_changes_when_description_changes() -> None:
    v1 = digest_catalog([_summary("alpha", "Alpha desc v1")])
    v2 = digest_catalog([_summary("alpha", "Alpha desc v2")])

    assert v1 != v2


def test_digest_changes_when_a_skill_is_added_or_removed() -> None:
    one = digest_catalog([_summary("alpha", "Alpha desc")])
    two = digest_catalog([_summary("alpha", "Alpha desc"), _summary("beta", "Beta desc")])

    assert one != two


def test_digest_ignores_fields_outside_name_and_description() -> None:
    low_rank = _summary("alpha", "Same desc", rank=100, source="/one")
    high_rank = _summary("alpha", "Same desc", rank=600, source="/two")

    assert digest_catalog([low_rank]) == digest_catalog([high_rank])


def test_digest_of_empty_catalog_is_stable() -> None:
    assert digest_catalog([]) == digest_catalog([])


# --- sync_catalog --------------------------------------------------------------


def test_sync_catalog_appends_first_event_when_none_exists() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([_summary("alpha", "Alpha desc")])

    sync_catalog(conversation_id, registry)

    events = core_events.read_events(conversation_id)
    assert len(events) == 1
    assert events[0].type == EVENT_TYPE
    assert events[0].payload["summaries"] == [
        {
            "name": "alpha",
            "description": "Alpha desc",
            "when_to_use": None,
            "user_invocable": True,
            "rank": 100,
            "source": "/fake/source",
        }
    ]
    assert events[0].payload["digest"] == digest_catalog(registry.discover())
    assert events[0].payload["empty"] is False


def test_sync_catalog_is_a_noop_when_digest_unchanged() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([_summary("alpha", "Alpha desc")])

    sync_catalog(conversation_id, registry)
    sync_catalog(conversation_id, registry)  # identical catalog content again

    events = core_events.read_events(conversation_id)
    assert len(events) == 1


def test_sync_catalog_appends_new_event_on_change_and_never_edits_prior() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([_summary("alpha", "Alpha desc")])

    sync_catalog(conversation_id, registry)
    first_payload_snapshot = dict(core_events.read_events(conversation_id)[0].payload)

    registry.summaries = [_summary("alpha", "Alpha desc"), _summary("beta", "Beta desc")]
    sync_catalog(conversation_id, registry)

    events = core_events.read_events(conversation_id)
    assert len(events) == 2
    # The prior event is untouched -- appended alongside, never mutated in
    # place. (Content equality, not object identity: the real event log
    # reconstructs a fresh TapestryEvent per read_events() call even when
    # the underlying row never changed, so `is` isn't the right check here.)
    assert events[0].payload == first_payload_snapshot
    assert events[1].payload["digest"] != events[0].payload["digest"]
    assert {s["name"] for s in events[1].payload["summaries"]} == {"alpha", "beta"}


def test_sync_catalog_empty_catalog_sets_explicit_empty_flag() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([])

    sync_catalog(conversation_id, registry)

    events = core_events.read_events(conversation_id)
    assert len(events) == 1
    assert events[0].payload["summaries"] == []
    assert events[0].payload["empty"] is True


def test_sync_catalog_transition_to_empty_appends_a_new_event() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([_summary("alpha", "Alpha desc")])
    sync_catalog(conversation_id, registry)

    registry.summaries = []
    sync_catalog(conversation_id, registry)

    events = core_events.read_events(conversation_id)
    assert len(events) == 2
    assert events[1].payload["empty"] is True
    assert events[1].payload["summaries"] == []


def test_sync_catalog_reappending_after_transition_to_empty_is_still_a_noop() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([])

    sync_catalog(conversation_id, registry)
    sync_catalog(conversation_id, registry)

    events = core_events.read_events(conversation_id)
    assert len(events) == 1


def test_sync_catalog_uses_expected_event_type() -> None:
    conversation_id = _new_conversation_id()
    registry = _StubRegistry([_summary("alpha", "Alpha desc")])

    sync_catalog(conversation_id, registry)

    event = core_events.read_events(conversation_id)[0]
    assert event.type == "skill/catalog" == EVENT_TYPE
