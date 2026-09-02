"""The provider-neutral human-question contract every chat surface renders.

`ask_user` is the single seam every adapter (Discord, Telegram, web) and
`approvals.py` go through to ask a human something and get a structured
answer back — options + multi-select + free-text override, with a tagged
`intent` that changes *presentation* only, never how the answer is encoded.
Discord renders it as buttons, Telegram as an inline keyboard, the web UI as
custom controls; a plain-text reply is the universal fallback everywhere.

v1 implementation is intentionally simple: append an `ask/requested` event,
then poll the event log for a matching `ask/answered` event appended by
whichever adapter the human responded on. A real pub/sub mechanism (e.g. an
asyncio.Event registry, or a DB trigger/notify) can replace the polling loop
later without changing this function's signature — every adapter already
only depends on `ask_user`'s contract, not its internals.
"""

from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel

from tapestry.core import events

# Kept as module-level constants (rather than function parameters) so the
# public signature stays exactly `ask_user(conversation_id, questions)` per
# the agreed contract, while tests can still monkeypatch these down to
# exercise the timeout path quickly.
POLL_INTERVAL_SECONDS = 0.5
ASK_TIMEOUT_SECONDS = 900


class AskTimeoutError(Exception):
    """Raised when no matching ask/answered event arrives within the timeout."""


class AskQuestion(BaseModel):
    id: str
    question: str
    detail: str | None = None
    options: list[str] | None = None
    multi_select: bool = False
    # e.g. "approval", "plan-review", "clarification" — presentation hint
    # only; never changes how AskAnswer is shaped or encoded.
    intent: str = "generic"
    # ADDITIVE, non-breaking schema extension (added for graph/build.py's
    # approval node): when an ask is about a specific task's proposed diff
    # (a file-editor/terminal/git/deploy tool call awaiting human sign-off),
    # this carries that task's id so a consumer (e.g. the web app's
    # /conversation/[id]/diff/[taskId] screen) can find "the approval
    # question for this specific diff" directly, instead of guessing via a
    # same-conversation heuristic. None for every ask unrelated to a task
    # (plain clarifications, plan reviews, etc.) — existing callers that
    # never set it are unaffected.
    related_task_id: str | None = None


class AskAnswer(BaseModel):
    id: str
    selected: list[str] | None = None
    custom: str | None = None


async def ask_user(conversation_id: str, questions: list[AskQuestion]) -> list[AskAnswer]:
    """Ask a human one or more questions and wait for their answer(s).

    Appends one `ask/requested` event carrying all `questions` in its
    payload, then polls for a single `ask/answered` event whose
    `payload["request_id"]` equals the request event's id and whose
    `payload["answers"]` is a list of answer dicts (matching `AskAnswer`,
    keyed by each question's `id`) — one adapter-side reply covers the
    whole batch of questions in one event, mirroring how `questions` is
    passed in as one batch.

    Raises `AskTimeoutError` if no matching answer arrives within
    `ASK_TIMEOUT_SECONDS`.
    """
    request_event = events.append_event(
        conversation_id=conversation_id,
        type="ask/requested",
        actor="system",
        payload={"questions": [q.model_dump() for q in questions]},
    )

    # A monotonic wall-clock deadline, not an accumulator of intended sleep
    # durations -- asyncio.sleep() can overshoot under load, and each poll
    # iteration also spends real time on a synchronous DB round-trip, so
    # counting "how many times we slept 0.5s" would silently run longer
    # than ASK_TIMEOUT_SECONDS actually promises.
    deadline = time.monotonic() + ASK_TIMEOUT_SECONDS
    while True:
        recent_events = events.read_events(conversation_id, since=request_event.timestamp)
        for event in recent_events:
            if (
                event.type == "ask/answered"
                and event.payload.get("request_id") == request_event.id
            ):
                answers_payload = event.payload.get("answers", [])
                return [AskAnswer(**answer) for answer in answers_payload]

        if time.monotonic() >= deadline:
            raise AskTimeoutError(
                f"no answer received for ask/requested {request_event.id!r} "
                f"in conversation {conversation_id!r} within "
                f"{ASK_TIMEOUT_SECONDS}s"
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
