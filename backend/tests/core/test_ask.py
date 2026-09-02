from __future__ import annotations

import asyncio

import pytest

from tapestry.core import ask as ask_module
from tapestry.core.ask import AskAnswer, AskQuestion, AskTimeoutError, ask_user
from tapestry.core.events import append_event, read_events


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    # Keep tests fast without changing ask_user's public signature.
    monkeypatch.setattr(ask_module, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(ask_module, "ASK_TIMEOUT_SECONDS", 0.2)


@pytest.mark.asyncio
async def test_ask_user_appends_ask_requested_event():
    question = AskQuestion(id="q1", question="Deploy now?", options=["yes", "no"])

    async def answer_soon():
        await asyncio.sleep(0.02)
        events = read_events("conv-1")
        request_event = next(e for e in events if e.type == "ask/requested")
        append_event(
            "conv-1",
            "ask/answered",
            "human",
            {
                "request_id": request_event.id,
                "answers": [{"id": "q1", "selected": ["yes"], "custom": None}],
            },
        )

    asyncio.create_task(answer_soon())
    await ask_user("conv-1", [question])

    events = read_events("conv-1")
    request_events = [e for e in events if e.type == "ask/requested"]
    assert len(request_events) == 1
    assert request_events[0].payload["questions"][0]["id"] == "q1"
    assert request_events[0].payload["questions"][0]["question"] == "Deploy now?"


@pytest.mark.asyncio
async def test_ask_user_returns_matching_answer_once_it_arrives():
    question = AskQuestion(id="q1", question="Approve?", options=["approve", "reject"])

    async def answer_soon():
        await asyncio.sleep(0.02)
        events = read_events("conv-1")
        request_event = next(e for e in events if e.type == "ask/requested")
        append_event(
            "conv-1",
            "ask/answered",
            "human",
            {
                "request_id": request_event.id,
                "answers": [{"id": "q1", "selected": ["approve"], "custom": None}],
            },
        )

    asyncio.create_task(answer_soon())
    answers = await ask_user("conv-1", [question])

    assert len(answers) == 1
    assert isinstance(answers[0], AskAnswer)
    assert answers[0].id == "q1"
    assert answers[0].selected == ["approve"]


@pytest.mark.asyncio
async def test_ask_user_ignores_answers_for_a_different_request():
    question = AskQuestion(id="q1", question="Approve?", options=["approve", "reject"])

    # An answer to some unrelated, earlier request must not satisfy this ask.
    append_event(
        "conv-1",
        "ask/answered",
        "human",
        {
            "request_id": "some-other-request-id",
            "answers": [{"id": "q1", "selected": ["reject"], "custom": None}],
        },
    )

    with pytest.raises(AskTimeoutError):
        await ask_user("conv-1", [question])


@pytest.mark.asyncio
async def test_ask_user_raises_ask_timeout_error_when_unanswered():
    question = AskQuestion(id="q1", question="Anyone there?")

    with pytest.raises(AskTimeoutError):
        await ask_user("conv-1", [question])
