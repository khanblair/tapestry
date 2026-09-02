"""Self-verification before a persona declares a task done.

Per the scoped spec's "Loop Engineering" section: nobody researched solves
this (verified: not in LangGraph, not in openhands-tools, not in Hermes Bot
Mode, not in DeepSeek Harness — see `docs/vendor-research/ANALYSIS-
deepseek-harness.md` §2). This is genuine design work, not integration.

`verify_before_completion` mirrors the real procedure in
`skills/verification-before-completion/SKILL.md` (read directly, not
paraphrased from memory) — its four checks, in order:

    1. Restate the original ask, in your own words.
    2. Re-read your own diff, top to bottom, as if reviewing someone else's
       PR (leftover debug code, unintended changes, scope creep/gaps,
       TODOs/placeholders).
    3. Re-run the tests and read the actual output (not "should pass").
    4. Check every acceptance criterion the original ask actually named,
       individually.

This module asks the model to actually perform those four checks against
real, re-read evidence (recent `tool/result` / `task/diff_ready` events
pulled from the conversation's own event log — not the model's memory of
having done the work) and answer in strict, parseable JSON. If the model's
answer can't be parsed as that JSON shape, this FAILS CLOSED: `passed=False`
with the raw, unparseable text preserved in `notes`, never defaulted to
`passed=True`. A verification gate that quietly passes on malformed output
is worse than no gate at all.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from tapestry.core import events
from tapestry.core.personas import Persona
from tapestry.models.litellm_client import call_model

__all__ = ["VerificationResult", "verify_before_completion"]

# How many of the most recent relevant events (tool/result, task/diff_ready,
# assistant/message) to feed back to the model as "your own diff/output to
# re-read" — bounded so a long-running task doesn't blow the context window
# just to verify itself. Recency is what matters for "what did I just do."
MAX_RECENT_EVENTS = 20

_RELEVANT_EVENT_TYPES = {"tool/result", "task/diff_ready", "assistant/message"}

_SYSTEM_PROMPT = """You are running a mandatory self-verification pass before a task can be \
declared complete. Do not be agreeable. Your job is to catch a task that \
looks done but isn't, not to rubber-stamp it.

Follow these four checks, in order, using ONLY the evidence given to you \
below (your own prior actions in this conversation) — not what you assume \
you probably did:

1. Restate the original ask in your own words. If it had multiple parts, \
name each part separately.
2. Re-read your own diff/output, top to bottom, as if reviewing someone \
else's PR: leftover debug code, unintended changes, scope creep or scope \
gaps, TODOs/placeholders left unfinished.
3. State whether tests were actually run (per the evidence given) and what \
their actual result was. "Should pass" is not evidence. If no test-running \
evidence is present at all, say so plainly — do not assume tests passed.
4. Check every acceptance criterion the original ask explicitly named, \
individually.

Respond with ONLY a single JSON object (no markdown fences, no prose \
outside the JSON) with exactly these keys:
{
  "restated_ask": "<your restatement from check 1>",
  "diff_review": "<findings from check 2, or 'no issues found'>",
  "tests_run": <true|false>,
  "test_result": "<what the evidence shows, or 'no test evidence found'>",
  "acceptance_criteria_check": "<per-criterion findings from check 4>",
  "passed": <true|false>,
  "notes": "<one paragraph summary of why passed is true or false>"
}
"passed" must be false if any check above found a real problem, if tests \
were not actually run, or if any named acceptance criterion is unmet."""


class VerificationResult(BaseModel):
    passed: bool
    notes: str


def _format_recent_events(conversation_id: str) -> str:
    """Render the most recent relevant events as readable text for the
    verification prompt — the "re-read your own diff/output" evidence.
    """
    all_events = events.read_events(conversation_id)
    relevant = [event for event in all_events if event.type in _RELEVANT_EVENT_TYPES]
    recent = relevant[-MAX_RECENT_EVENTS:]

    if not recent:
        return "(no tool results or diffs recorded for this conversation yet)"

    lines = []
    for event in recent:
        payload_text = json.dumps(event.payload, sort_keys=True)
        lines.append(f"- [{event.type}] actor={event.actor}: {payload_text}")
    return "\n".join(lines)


def _parse_model_json(text: str) -> dict | None:
    """Best-effort parse of the model's JSON response.

    Tries the raw text first, then falls back to extracting the first
    `{...}` span (models occasionally wrap JSON in prose despite
    instructions not to). Returns None — never raises — on any failure, so
    the caller can fail closed.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


async def verify_before_completion(
    conversation_id: str, task_description: str, persona: Persona
) -> VerificationResult:
    """Run the four-check self-verification pass before a task may close.

    Calls `models.litellm_client.call_model` (using `persona`'s own model —
    a persona verifies its own work with the same model it did the work
    with, matching the SKILL.md's framing of this as something every
    persona does for itself, not an external reviewer step) with the
    original ask and the conversation's own recent tool-result/diff events
    as evidence, and asks it to answer the four SKILL.md checks as
    structured JSON.

    Fails closed: if the model's response can't be parsed into the
    expected JSON shape, returns `VerificationResult(passed=False, notes=
    <raw response text>)` rather than assuming success. This function never
    raises on a malformed model response — a broken verification pass is
    reported as "not verified," which is the safe default for a gate that
    sits in front of `task/completed`.
    """
    evidence = _format_recent_events(conversation_id)
    user_prompt = (
        f"Original ask:\n{task_description}\n\n"
        f"Recent recorded actions/results in this conversation "
        f"(most recent last, up to {MAX_RECENT_EVENTS}):\n{evidence}\n\n"
        "Run the four-check verification now and respond with the JSON "
        "object described in your instructions."
    )

    response = await call_model(
        model=persona.model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    parsed = _parse_model_json(response.text)
    if parsed is None:
        return VerificationResult(
            passed=False,
            notes=(
                "Verification response could not be parsed as JSON; failing "
                f"closed. Raw model output: {response.text!r}"
            ),
        )

    passed = parsed.get("passed")
    if not isinstance(passed, bool):
        return VerificationResult(
            passed=False,
            notes=(
                "Verification response JSON was missing a boolean 'passed' "
                f"field; failing closed. Parsed response: {parsed!r}"
            ),
        )

    notes_parts = [
        f"Restated ask: {parsed.get('restated_ask', '(not provided)')}",
        f"Diff review: {parsed.get('diff_review', '(not provided)')}",
        f"Tests run: {parsed.get('tests_run', '(not provided)')} — "
        f"{parsed.get('test_result', '(not provided)')}",
        f"Acceptance criteria: {parsed.get('acceptance_criteria_check', '(not provided)')}",
        f"Summary: {parsed.get('notes', '(not provided)')}",
    ]
    return VerificationResult(passed=passed, notes="\n".join(notes_parts))
