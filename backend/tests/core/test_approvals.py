from __future__ import annotations

import pytest

from tapestry.core import approvals as approvals_module
from tapestry.core.approvals import request_approval
from tapestry.core.ask import AskAnswer, AskQuestion


class _StubAskUser:
    """Captures the question(s) it was called with and returns canned answers."""

    def __init__(self, answers: list[AskAnswer]):
        self.answers = answers
        self.calls: list[tuple[str, list[AskQuestion]]] = []

    async def __call__(self, conversation_id, questions):
        self.calls.append((conversation_id, questions))
        return self.answers


@pytest.mark.asyncio
async def test_request_approval_builds_an_approval_intent_question(monkeypatch):
    stub = _StubAskUser([AskAnswer(id=stub_id, selected=["approve"]) for stub_id in ["q"]])
    monkeypatch.setattr(approvals_module, "ask_user", stub)

    await request_approval("conv-1", "Ship it?", "Deploys main to prod")

    assert len(stub.calls) == 1
    conversation_id, questions = stub.calls[0]
    assert conversation_id == "conv-1"
    assert len(questions) == 1
    question = questions[0]
    assert question.question == "Ship it?"
    assert question.detail == "Deploys main to prod"
    assert question.options == ["approve", "reject"]
    assert question.multi_select is False
    assert question.intent == "approval"


@pytest.mark.asyncio
async def test_request_approval_returns_true_when_approved(monkeypatch):
    stub = _StubAskUser([AskAnswer(id="q", selected=["approve"])])
    monkeypatch.setattr(approvals_module, "ask_user", stub)

    result = await request_approval("conv-1", "title", "description")

    assert result is True


@pytest.mark.asyncio
async def test_request_approval_returns_false_when_rejected(monkeypatch):
    stub = _StubAskUser([AskAnswer(id="q", selected=["reject"])])
    monkeypatch.setattr(approvals_module, "ask_user", stub)

    result = await request_approval("conv-1", "title", "description")

    assert result is False


@pytest.mark.asyncio
async def test_request_approval_returns_false_when_no_selection(monkeypatch):
    stub = _StubAskUser([AskAnswer(id="q", selected=None, custom="maybe later")])
    monkeypatch.setattr(approvals_module, "ask_user", stub)

    result = await request_approval("conv-1", "title", "description")

    assert result is False


@pytest.mark.asyncio
async def test_request_approval_returns_false_when_no_answers_at_all(monkeypatch):
    stub = _StubAskUser([])
    monkeypatch.setattr(approvals_module, "ask_user", stub)

    result = await request_approval("conv-1", "title", "description")

    assert result is False
