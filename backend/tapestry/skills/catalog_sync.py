"""Keeps the skill catalog coherent inside the append-only event log.

This is the content-hash-then-replace technique verified from DeepSeek
Harness (`docs/vendor-research/ANALYSIS-deepseek-harness.md` §3) — described
there as the single most novel technique in the whole report, and explicitly
called out as a GENERAL pattern applicable to any dynamic catalog a persona
needs kept coherent across turns, not just skills:

    Content-hash a piece of externally-mutable state, and only append a new
    immutable log event when the hash changes, replacing — never mutating —
    the model's view of it.

Concretely: a SHA-256 digest is computed over the canonical (name,
description) pairs of the current skill catalog. That digest is compared
against the digest embedded in the most recently appended `skill/catalog`
event for the conversation. A brand-new, full-replacement event is appended
ONLY when there was no prior catalog event at all, or when the digest
changed — the prior event is never edited or removed. A reader always treats
the latest `skill/catalog` event as the current truth; anything older is
inert history.

Contract this module codes against (per the shared cross-team contract —
`core/events.py` may not exist as a real file yet at the time this module is
written, but WILL by the time anything runs together):

    def append_event(conversation_id: str, type: str, actor: str, payload: dict) -> TapestryEvent

`read_events` is this module's own reasonable assumption about the read-side
counterpart, since only `append_event`'s signature was specified up front:

    def read_events(conversation_id: str) -> list[TapestryEvent]

returning every event for the conversation in append (chronological) order.
Only two attributes of a returned `TapestryEvent` are relied on here —
`.type` (str) and `.payload` (dict) — so this module stays correct even if
the real `TapestryEvent` carries additional fields (id, actor, created_at,
...) this code never needed to know about.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from tapestry.core.events import append_event, read_events
from tapestry.skills.registry import SkillRegistry, SkillSummary

EVENT_TYPE = "skill/catalog"

# Actor recorded on catalog events. This is a system-generated bookkeeping
# event, not authored by any persona or the human — "system" is this
# module's own judgment call in the absence of a defined actor vocabulary
# from core/events.py; revisit if/when core/ defines one.
CATALOG_SYNC_ACTOR = "system"


class _EventLike(Protocol):
    type: str
    payload: dict[str, Any]


def digest_catalog(summaries: list[SkillSummary]) -> str:
    """SHA-256 hex digest over the canonical JSON of the catalog's
    `(name, description)` pairs, sorted by name for determinism — so the
    digest depends only on catalog CONTENT, never on filesystem scan order.
    """
    pairs = sorted(((s.name, s.description) for s in summaries), key=lambda pair: pair[0])
    canonical = json.dumps(pairs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_catalog_event(conversation_id: str) -> _EventLike | None:
    """The most recently appended `skill/catalog` event for this
    conversation, or None if the catalog has never been synced yet.
    Assumes `read_events` returns events in append (chronological) order.
    """
    events = read_events(conversation_id)
    catalog_events = [event for event in events if getattr(event, "type", None) == EVENT_TYPE]
    return catalog_events[-1] if catalog_events else None


def sync_catalog(conversation_id: str, registry: SkillRegistry) -> None:
    """Compute the current skill catalog's digest and compare it against the
    conversation's most recently recorded `skill/catalog` event.

    - No prior catalog event exists -> append one (first time).
    - Prior event exists but its digest differs from the current one ->
      append a NEW event (full replacement — the prior event is left alone).
    - Prior event exists and the digest matches -> do nothing. The catalog
      is already coherent in the log; appending would just be log noise.

    Never edits or removes a previous `skill/catalog` event — only appends.
    """
    summaries = registry.discover()
    digest = digest_catalog(summaries)

    prior = _last_catalog_event(conversation_id)
    if prior is not None and prior.payload.get("digest") == digest:
        return

    payload = {
        "summaries": [summary.model_dump() for summary in summaries],
        "digest": digest,
        # Explicit even when summaries was already empty before this call —
        # cheap to include always, and it's exactly the signal a reader
        # needs to render "no skills available, forget earlier names."
        "empty": len(summaries) == 0,
    }
    append_event(conversation_id, EVENT_TYPE, CATALOG_SYNC_ACTOR, payload)
