# Tapestry: Modes, Model Switching, and Expanded Personas

**Status: scoped, not built.** This is a follow-on to `tapestry_scoped_spec.md`
(status: built) — the v1 system is real and working; this document scopes
three additive features on top of it. Nothing here changes v1 behavior by
default: every new mode/field has a default that reproduces exactly what
Tapestry already does today.

Written after deep research into two real reference systems, not from
assumption:
- **Claude Code's own permission-mode system** (Auto / Manual / Accept edits
  / Plan / Bypass permissions) — direct first-hand knowledge, since this
  document was written by an agent operating under exactly that system.
- **Hermes Agent**, a real, mature, local codebase
  (`~/.hermes/hermes-agent/`), researched by reading its actual source (not
  just its own docs) for its approval-mode system, model-switching, and
  persona/agent config schema. See "What we verified in Hermes" call-outs
  throughout — every claim about Hermes below is backed by a real file path.

---

## 1. Modes

### 1.1 What exists today (the baseline every mode must reproduce as an option)

`graph/build.py`'s `persona_node` decides, per proposed tool call, whether it
needs human approval — today via one flat check:

```python
# graph/build.py:772 (current)
if tool_name in TOOLS_REQUIRING_APPROVAL:
    ...
    next_node = "approval"
else:
    next_node = "execute"
```

`TOOLS_REQUIRING_APPROVAL = frozenset({"file_editor", "terminal", "git",
"deploy_pipeline"})` — one flat set, no risk gradation. Everything else
(`file_editor_read`, `terminal_read_only`, `test_runner`, `skill_loader`) is
already always-auto. **This flat check *is* today's "Manual" mode outcome** —
nothing needs to change for Manual; it's the existing behavior, made
selectable rather than the only option.

Per-persona autonomy is currently expressed the only way it can be: by what
tools a persona's YAML lists at all. Ada has no mutating tools, so she can
never trigger the approval path regardless of mode — she has always been a
"Plan"-shaped persona by construction, not by any mode setting. This matters
for §1.4.

### 1.2 The one constraint that shapes everything below

`interrupt()` (called inside `approval_node`) re-executes its entire node
from the top on every resume — this is documented at length in
`graph/build.py`'s own module docstring and is why `approval_node` and
`execute_node` are separate nodes today. **A mode that auto-approves must
be a routing decision made in `persona_node`, before `approval_node` is ever
reached — never a conditional inside `approval_node` that skips
`interrupt()`.** `approval_node` itself does not change at all in this
design. Only the `if tool_name in TOOLS_REQUIRING_APPROVAL: next_node =
"approval"` branch in `persona_node` gains a mode-aware condition.

### 1.3 Tool risk tiers (the schema change modes actually require)

`TOOLS_REQUIRING_APPROVAL` as one flat set can express Manual and Bypass
(gate everything / gate nothing) but not "Accept edits" (gate everything
*except file edits*) or Auto's tiered allow-list. It becomes a tier map:

```python
TOOL_RISK_TIER: dict[str, Literal["safe", "edit", "mutate", "deploy"]] = {
    "file_editor_read": "safe",
    "terminal_read_only": "safe",
    "test_runner": "safe",
    "skill_loader": "safe",
    "file_editor": "edit",
    "terminal": "mutate",
    "git": "mutate",
    "deploy_pipeline": "deploy",
}
```

Four tiers, not more — resist adding granularity nothing here needs yet.
`"safe"` never gates, in any mode. `"deploy"` is its own tier specifically so
Auto's allow-list (§1.4) can treat production deploys differently from a
`git commit`, without needing a fifth mode.

### 1.4 What each mode resolves to

| Mode | `safe` | `edit` (file_editor) | `mutate` (terminal, git) | `deploy` |
|---|---|---|---|---|
| **Manual** (baseline) | auto | ask | ask | ask |
| **Accept edits** | auto | auto | ask | ask |
| **Auto** | auto | allow-list or guardian (§1.5) | guardian-screened | ask (always) |
| **Plan** | auto (read-only tools only reachable) | — | — | — |
| **Bypass permissions** | auto | auto | auto | auto |

**Bypass** is deliberately total, matching Claude Code's own "Bypass
permissions — accepts all permissions": no tier is exempt. This is a
high-trust mode a human opts into explicitly (§1.6's per-conversation
override), not a default anyone lands in by accident.

**Plan** is not a fifth entry in the tier table — it's a *toolset filter*
applied before the tier check ever runs: while Plan is active,
`persona_node` only offers the **intersection** of the persona's own tool
list with the read-only tools (`file_editor_read`, `terminal_read_only`,
`test_runner`, `skill_loader`) to the model, so nothing in the
`edit`/`mutate`/`deploy` tiers is even a reachable proposal *for that turn*
— regardless of what the persona's YAML otherwise allows.

Deliberately intersecting, not replacing: Ada's YAML keeps its current
hard-restricted toolset (`file_editor_read`, `terminal_read_only`) exactly
as it is today — a structural guarantee, not a mode-dependent one. Plan
mode is what makes an otherwise-unrestricted persona (Rex, say) behave like
Ada for one turn; it does nothing further to Ada, since her toolset was
already a subset of what Plan mode intersects down to. This makes §5
phase 3 purely additive — no existing persona file changes, and Ada keeps
her stronger-than-mode-dependent guarantee that she structurally cannot
mutate anything, mode setting notwithstanding. (An earlier draft of this
section proposed loosening Ada's toolset and relying on Plan-as-default
instead — deliberately not what's specified here, since it would trade a
hard guarantee for a soft one on the one persona whose entire design point
is "plans, never edits.")

### 1.5 Auto mode: allow-list *and* guardian, layered — not a choice between them

Per your answer, both. They compose naturally because they're solving
different parts of the problem:

- The **tier table** (§1.3) is the deterministic, free, always-on floor.
  `safe` always auto-approves, `deploy` always asks, in every mode including
  Auto — no model call, no ambiguity, fully testable.
- The **guardian** — a second, cheap model call that reviews one gated
  proposal and returns approve/deny/escalate — is reserved for exactly the
  tiers where a fixed rule can't confidently decide: `edit` and `mutate`.
  It runs synchronously inside `persona_node`, before the `next_node`
  decision (safe per §1.2 — persona_node has no `interrupt()` re-execution
  hazard anywhere in its body, not just at the top). A guardian **approve**
  sets `next_node = "execute"` directly, skipping the `ask/requested` event
  and the human prompt entirely. A guardian **deny** or **escalate** falls
  through to the exact same `next_node = "approval"` path Manual already
  uses — Auto never invents a new user-facing shape, it just sometimes
  avoids needing one.
- `deploy` tier is never guardian-eligible, in any mode — matching Nova's
  existing "most gated persona in the registry, starts paused" design intent
  from her own YAML comment. A production deploy always surfaces to a human,
  even in Auto.

> **What we verified in Hermes:** this is a real, working pattern there —
> `approvals.mode = "smart"` (`hermes_cli/config_defaults.py:2539`) is the
> default, and its `_smart_approve()` (`tools/approval.py:5064`) is
> explicitly credited to OpenAI Codex's "Smart Approvals." Hermes's own
> gating is narrower than Tapestry's, though — it only ever runs through two
> functions, `check_all_command_guards`/`check_execute_code_guard`
> (`tools/approval.py:4720`, `5430`), meaning file writes and most tool
> calls are *not* gated by mode at all unless a plugin explicitly opts in.
> Tapestry's four-tool, tier-based design is deliberately more uniform than
> that — every mutating tool is covered, not just shell/exec.

The guardian call needs its own model — see §2.4 (auxiliary model), so it
isn't spending the persona's own (possibly expensive) configured model on
every gated proposal.

### 1.6 Where a mode lives: persona default + conversation override

Neither purely static (baked into the persona) nor purely a global session
toggle — both real systems mix these, and so should Tapestry:

- **`Persona.default_mode`** (new field, §3) — what a persona starts in.
  Ada defaults to `"plan"`, Nova to `"manual"` (or stricter still — see
  §1.4's note that `deploy` always asks regardless of mode, which already
  covers most of what made Nova cautious), Rex/Vex default to `"manual"`
  too, preserving today's exact behavior for everyone until a human opts
  a conversation into something looser.
- **A per-conversation override**, settable from the conversation view
  (mirroring Claude Code's own mode switcher, which is a per-session
  control, not a per-agent-identity one). Stored as an event —
  `mode/changed` (`payload: {mode, persona_id}`) — consistent with every
  other piece of conversation state in this codebase being a log
  projection, not a second store. `persona_node` reads the most recent
  `mode/changed` event for the conversation (or falls back to
  `persona.default_mode` if none exists yet) at the top of its own body,
  the same way it already reads other conversation state.

---

## 2. Model switching

### 2.1 What exists today

`Persona.model` is one fixed string, set at persona-creation time, written
to `personas/<id>.yaml` via `save_persona`. `litellm_client.call_model`
already classifies failures (empty completion → retry,
`ContextWindowExceededError` → `TapestryContextWindowExceeded`) but **never
falls back to a different model or provider** — a real, clean gap, not a
missing nice-to-have. There is no way to change a persona's model for one
conversation without editing its YAML, which affects every conversation
that persona is in.

### 2.2 Three scopes, mirroring Hermes's `/model` command exactly

> **What we verified in Hermes:** `gateway/slash_commands.py:1751`'s
> `/model <name> [--once|--session|--global]`, backed by one shared pipeline
> (`hermes_cli/model_switch.py`). Once = snapshotted and restored after the
> next turn. Session = held in an in-memory per-session override, layered
> over config each turn. Global = persisted to the profile's `config.yaml`.

Tapestry's equivalent, using the event log instead of in-memory state
(consistent with everything else here — a restarted backend process should
not lose a session-scoped model override the way Hermes's in-memory
dictionary would):

- **Global**: `updatePersona(id, {model})` — already exists, unchanged.
  Writes `personas/<id>.yaml` via the existing endpoint.
- **Session** (this conversation only, until changed again): a new
  `persona/model_switched` event, `payload: {persona_id, model}`. Read the
  same way `mode/changed` is read — most recent event for the conversation
  wins, falls back to `Persona.model` if none exists.
- **Once** (next turn only): not event-logged at all — held in
  `TapestryGraphState` for exactly one `persona_node` pass (a new
  `model_override_once: str | None` state field, cleared after use,
  identical in spirit to `pending_tool_call`'s single-use lifecycle). This
  one is genuinely ephemeral and doesn't belong in durable history.

### 2.3 Fallback chain (the real gap the research surfaced)

New optional `Persona.fallback_models: list[str]` field. The chain lives in
`litellm_client.call_model` itself, not in `persona_node` — `call_model` is
already the one place that classifies a failure as retryable-and-exhausted
vs. a content/logic failure (empty completion, `ContextWindowExceededError`,
...); walking the fallback chain anywhere else would mean either
re-deriving that classification a second time or leaking `call_model`'s
internal retry state up into `persona_node`. Concretely:

```python
async def call_model(
    model: str,
    messages: list[dict],
    tools: list[dict],
    max_retries: int = 2,
    fallback_models: list[str] = (),
) -> ModelResponse:
    ...
```

On a retryable-and-exhausted failure against `model`, `call_model` advances
to the next entry in `fallback_models` and retries against *that* model
before giving up — `fallback_models` is only ever consulted after
`max_retries` against the current model is spent, not as a first resort.
`ModelResponse` gains a `model_used: str` field (defaults to the originally
requested `model`) so `persona_node` can tell a fallback happened without
re-deriving anything: `if response.model_used != persona.model: log
model/fallback`. `persona_node`'s own job stays exactly what it is today —
call `call_model`, log the result — the chain-walking policy stays fully
inside the module that already owns failure classification.

`model/fallback` event, `payload: {from_model, to_model, reason}`, logged by
`persona_node` off `ModelResponse.model_used` as above, so a human reviewing
the conversation can see it happened. Reverting to the primary model on the
next fresh task (not mid-task) keeps this simple — no exponential-backoff
re-probing machinery like Hermes's `try_activate_fallback`
(`agent/chat_completion_helpers.py:2459`) is needed at Tapestry's current
scale; that's real complexity Hermes carries for a much higher-traffic
multi-tenant system, not something to import wholesale.

### 2.4 Auxiliary model (small, needed for §1.5, not a general framework)

One new optional field, `Persona.guardian_model: str | None` (falls back to
a global default env var, e.g. `TAPESTRY_GUARDIAN_MODEL`, when unset) — the
model Auto mode's guardian check (§1.5) calls. This is deliberately *not*
Hermes's full `auxiliary.<task>.*` system (separate config for vision,
compression, and other side-tasks) — Tapestry has exactly one side-task that
needs a cheap model right now. Generalizing to an auxiliary-task framework
before a second real use case exists would be speculative.

---

## 3. Expanded Persona schema

```python
class Persona(BaseModel):
    id: str
    name: str
    role: str
    model: str
    fallback_models: list[str] = []          # new, §2.3
    guardian_model: str | None = None         # new, §2.4
    reasoning_effort: str | None = None       # new — passed through to
                                               # LiteLLM as-is; provider
                                               # support varies, so this is
                                               # optional and unvalidated
                                               # against a fixed enum
    system_prompt: str
    tools: list[str]
    mcp_servers: list[str]
    default_mode: Literal[
        "manual", "accept_edits", "auto", "plan", "bypass"
    ] = "manual"                              # new, §1.6
    max_turns: int | None = None              # new — overrides
                                               # budgets.DEFAULT_MAX_TURNS
                                               # when set; wiring, not new
                                               # machinery (check_turn_budget
                                               # already takes this as a
                                               # parameter)
    max_delegation_depth: int | None = None   # new — same story for
                                               # DEFAULT_MAX_DELEGATION_DEPTH
    status: Literal["online", "busy", "paused", "offline"]
    color: str
```

Every new field is optional with a default that reproduces today's exact
behavior — loading an existing `personas/*.yaml` with none of these fields
present works unchanged.

### 3.1 Deliberately not adopted from Hermes's schema

Hermes's own agent config is far larger than this. Cut, with reasons:

- **Memory config** (`memory.memory_enabled`, `memory.provider`, ...) —
  Tapestry has no memory subsystem at all. A config field for a subsystem
  that doesn't exist is worse than no field; it invites building the field
  before anyone's decided whether Tapestry wants persistent cross-
  conversation persona memory at all. That's a separate, much bigger design
  question than this document's scope.
- **Cron/scheduling** (`RoutineJob`) — no scheduler exists in Tapestry;
  same reasoning.
- **Avatar shape/image beyond `color`** — cosmetic, low value relative to
  everything else here, easy to add later without touching any of the
  mechanisms above.
- **Cost/rate budgets as a persona field** — `budgets.measure_conversation_cost`
  is *deliberately* non-gating today (see that module's own docstring: "a
  token/cost measurement informs a policy rather than gating by itself").
  Turning it into an enforced per-persona ceiling is a real, separate policy
  design (what happens when a budget is hit mid-task? mid-delegation-chain?)
  — worth its own scoping pass, not a field bolted on here. Notably, Hermes
  doesn't have this either (verified: no `$`/token ceiling field found
  anywhere in its schema) — this would be new ground for both projects, not
  a gap Tapestry is behind on.

### 3.2 "More/custom personas" is mostly already built — the gap is the form

`POST /api/personas` / `PATCH /api/personas/{id}` and the `/personas/new`
→ `PersonaEditForm` flow are real and working today (verified: `createPersona`/
`updatePersona` in `web/lib/api.ts`, wired end-to-end in
`web_adapter/api.py`). The actual narrowness is that **`PersonaEditForm`
only exposes four fields** — `name`, `role`, `model`, `systemPrompt`,
`tools` — and drops `mcp_servers` entirely even though it's already a real
field on `Persona`. So "more/custom personas" and "richer fields per
persona" turn out to be the same piece of frontend work: extend
`PersonaEditForm` to cover every field in §3's schema (including the
already-existing-but-unexposed `mcp_servers`), plus a mode picker and a
model/fallback-models picker reusing whatever component the conversation
view's own mode/model switcher (§4) uses.

---

## 4. Frontend surface (sketch — not wireframed)

- **Mode switcher**: a control in the conversation header (near the
  existing `···` menu, alongside where a future model switcher would live)
  showing the active mode for the conversation's lead persona, defaulting to
  that persona's `default_mode`. Changing it posts a new
  `POST /api/conversations/{id}/mode` (`{mode}`), which appends the
  `mode/changed` event from §1.6.
- **Model switcher**: similarly, a control offering the persona's
  configured `model` plus a manual override, calling a new
  `POST /api/conversations/{id}/model` (`{model, scope: "once"|"session"}`)
  — global scope already exists via the Persona Management screen's
  existing edit form and doesn't need a conversation-view control.
- **`PersonaEditForm`**: gains fields for every new `Persona` property in
  §3, plus the pre-existing-but-missing `mcp_servers` multi-select.
- **Guardian decisions surfaced, not hidden**: when Auto mode's guardian
  auto-approves something, the resulting `tool/result`/`task/diff_ready`
  messages already render via the fix landed just before this document (see
  the schema-widening work) — no new UI needed to *see* what happened, only
  to know *why* it wasn't asked about. Worth a small `activity.label`
  suffix like "(auto-approved)" sourced from the guardian's own event
  payload — a `mode/auto_approved` event, `payload: {tool_name, reason}`,
  cheap to add alongside `mode/changed`.

---

## 5. Build sequence

Each phase is independently shippable and testable; later phases build on
earlier ones but don't require them to be "done," matching this project's
established build-in-parallel pattern where it's safe to.

1. **Tool risk tiers + mode routing in `persona_node`** (§1.3, §1.4) —
   backend only, Manual/Accept-edits/Bypass first (pure routing-table logic,
   no new model calls). Fully testable against the existing graph test
   harness's real-approval-flow pattern.
2. **`mode/changed` event + per-conversation override read path** (§1.6) +
   **mode switcher endpoint/UI** (§4) — makes phase 1 actually reachable by
   a human instead of only a persona default.
3. **Plan mode** (§1.4) — the toolset-intersection filter. Purely additive:
   no existing persona file changes, since it intersects a persona's tools
   with the read-only set rather than replacing them.
4. **Guardian model for Auto** (§1.5, §2.4) — the one piece that costs a
   real model call; ship after 1–3 are proven, since Auto degrades
   gracefully to "allow-list only" if the guardian call is simply not
   implemented yet.
5. **Model switching**: once/session scopes (§2.2) — event/state plumbing,
   independent of modes.
6. **Fallback chain** (§2.3) — independent of everything else; can ship
   any time after `litellm_client`'s failure classification is confirmed
   stable.
7. **`Persona` schema fields + `PersonaEditForm` expansion** (§3, §3.2,
   §4) — the fields land alongside whichever phase needs them (e.g.
   `default_mode` with phase 2, `fallback_models` with phase 6); the form
   work is one pass at the end covering all of them plus `mcp_servers`.

---

## 6. Explicitly out of scope (this document)

Persona memory/context persistence, scheduling/cron, enforced cost budgets,
avatar imagery beyond color — see §3.1 for why each is cut, not just
deferred silently. If any of these turn out to be what "personas are too
narrow" was actually pointing at, that's a different, follow-on scoping
conversation, not an extension of this one.
