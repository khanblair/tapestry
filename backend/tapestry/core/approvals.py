"""Approval — one `intent` on top of ask.py, not its own protocol.

Every surface already knows how to render an `AskQuestion`; approval is just
the specific, common case of one question with a fixed `["approve",
"reject"]` option set and `intent="approval"` (which surfaces use purely as
a presentation hint — e.g. rendering as a prominent modal instead of an
inline prompt). There is no separate approval event type, storage, or
answer shape — it is answered exactly like any other ask, through the same
`ask/requested` / `ask/answered` event pair.
"""

from __future__ import annotations

import uuid

from tapestry.core.ask import AskQuestion, ask_user


async def request_approval(conversation_id: str, title: str, description: str) -> bool:
    """Ask a human to approve or reject one thing; return True iff approved.

    `title` becomes the question text, `description` its detail. Returns
    `True` only when the human's answer selected exactly `["approve"]`;
    any other answer (["reject"], no selection, free-text only, etc.) is
    treated as not-approved.
    """
    question = AskQuestion(
        id=uuid.uuid4().hex,
        question=title,
        detail=description,
        options=["approve", "reject"],
        multi_select=False,
        intent="approval",
    )
    answers = await ask_user(conversation_id, [question])
    if not answers:
        return False
    return answers[0].selected == ["approve"]
