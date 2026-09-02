"""Three genuinely separate budget mechanisms — not one counter.

Per the scoped spec's "Loop Engineering" section and
`docs/vendor-research/ANALYSIS-deepseek-harness.md`'s layered-budget
vocabulary:

1. **A durable, per-task round cap** (`TurnBudgetExceeded` /
   `check_turn_budget`) — how many persona-node -> execute-node cycles a
   single task can run before it's forced to stop. This is Tapestry's own
   addition, distinct from `core.delegation`'s round cap below: it bounds
   one persona's own tool-call loop (propose a tool call, get it executed,
   look at the result, propose another...), not persona-to-persona
   delegation.

2. **A persisted recursion-depth cap for delegation**
   (`DelegationDepthExceeded` / `check_delegation_depth`) — how many hops
   deep persona A -> persona B -> persona C -> ... can go before it's
   refused. This is intentionally separate from `core.delegation`'s
   *round* cap (which bounds repeated back-and-forth between the SAME two
   personas, scoped to the current turn) — depth bounds the length of a
   delegation CHAIN across different personas, and is carried in the
   graph's own checkpointed state (`delegation_depth`, see `build.py`'s
   `TapestryGraphState`), not recomputed from the event log, specifically
   so a resumed/replayed graph can't silently reset it back to 0. `core.
   delegation.delegate()`'s own `DelegationRoundLimitExceeded` is reused
   as-is by `build.py` at the same call site — this module does not
   reimplement it, only re-exports it for convenience so a caller doesn't
   need two imports to catch "anything delegation-budget-related".

3. **A cost/token measurement** (`measure_conversation_cost`) — sums
   whatever `model/response` events got logged for a conversation (`build.
   py`'s persona node appends one via `core.events.append_event` after
   every `models.litellm_client.call_model` call). This function only
   REPORTS a number; per the spec, a token/cost measurement "informs a
   policy rather than gating by itself" — nothing in this module raises
   based on cost. Enforcing a cost ceiling, if ever added, belongs to
   whoever reads this function's output (a future policy layer), not here.
"""

from __future__ import annotations

from tapestry.core import events
from tapestry.core.delegation import DelegationRoundLimitExceeded  # re-exported, see module docstring

__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "TurnBudgetExceeded",
    "DelegationDepthExceeded",
    "DelegationRoundLimitExceeded",
    "check_turn_budget",
    "check_delegation_depth",
    "measure_conversation_cost",
]

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_DELEGATION_DEPTH = 3

MODEL_RESPONSE_EVENT_TYPE = "model/response"


class TurnBudgetExceeded(Exception):
    """Raised when a single task's persona<->execute cycle count exceeds
    its cap. Not a delegation concept — this bounds one persona's own
    propose/execute/observe loop within a task, independent of whether
    that persona ever delegates to another.
    """


class DelegationDepthExceeded(Exception):
    """Raised when a persona-to-persona delegation CHAIN would exceed
    `max_depth` hops. Persisted via the graph's own checkpointed
    `delegation_depth` state field (see `build.py`) — deliberately not
    recomputed from the event log on every check, so a resumed/replayed
    subagent graph can't silently reset the count back to 0 the way a
    log-rescan-based cap could if the log were ever pruned or partially
    replayed.
    """


def check_turn_budget(turn_count: int, max_turns: int = DEFAULT_MAX_TURNS) -> None:
    """Raise `TurnBudgetExceeded` iff `turn_count` has already reached
    `max_turns`.

    Called at the TOP of the persona node, before it does anything else
    (including any event-log writes) — see `build.py`'s `persona_node`.
    `turn_count` is the number of persona<->execute cycles already
    completed for the current task; a call with `turn_count == max_turns`
    means the cap has been fully spent and this would-be `max_turns + 1`-th
    cycle must not start.
    """
    if turn_count >= max_turns:
        raise TurnBudgetExceeded(
            f"turn budget exceeded: turn_count={turn_count} >= max_turns={max_turns}"
        )


def check_delegation_depth(
    delegation_depth: int, max_depth: int = DEFAULT_MAX_DELEGATION_DEPTH
) -> None:
    """Raise `DelegationDepthExceeded` iff `delegation_depth` has already
    reached `max_depth`.

    Called before a persona is allowed to delegate to another persona
    (i.e. before `core.delegation.delegate()` is invoked) — see `build.py`'s
    delegation handling inside `persona_node`. `delegation_depth` is the
    number of delegation hops already taken to reach the CURRENTLY active
    persona; a call with `delegation_depth == max_depth` means one more hop
    would exceed the cap.
    """
    if delegation_depth >= max_depth:
        raise DelegationDepthExceeded(
            f"delegation depth exceeded: delegation_depth={delegation_depth} "
            f">= max_depth={max_depth}"
        )


def measure_conversation_cost(conversation_id: str) -> dict:
    """Sum `cost` / `input_tokens` / `output_tokens` across every
    `model/response` event logged for `conversation_id`.

    Returns a dict with keys `cost`, `input_tokens`, `output_tokens`
    (floats/ints, defaulting to 0 when nothing has been logged yet) and
    `call_count` (how many `model/response` events contributed). A logged
    event whose `cost`/`input_tokens`/`output_tokens` is `None` (e.g. the
    provider didn't report it) contributes 0 for that field rather than
    breaking the sum — matches `models.litellm_client.ModelResponse`, whose
    `cost`/`input_tokens`/`output_tokens` are all `| None`.

    This is a pure read — it does not gate or raise anything. Per the
    module docstring, cost/token measurement informs a policy elsewhere; it
    is not itself a budget enforcement mechanism.
    """
    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    call_count = 0

    for event in events.read_events(conversation_id):
        if event.type != MODEL_RESPONSE_EVENT_TYPE:
            continue
        call_count += 1
        total_cost += event.payload.get("cost") or 0.0
        total_input_tokens += event.payload.get("input_tokens") or 0
        total_output_tokens += event.payload.get("output_tokens") or 0

    return {
        "cost": total_cost,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "call_count": call_count,
    }
