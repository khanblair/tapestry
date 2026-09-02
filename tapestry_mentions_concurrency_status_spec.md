# Tapestry: Group Mentions, Turn Concurrency, and Live Status

**Status: §1 and §4 built and tested; §2 (tag-all itself) not yet built.**
Follow-on to `tapestry_scoped_spec.md` (built) and
`tapestry_modes_models_personas_spec.md` (built). Scopes three things the
user asked about, plus one severe pre-existing bug found while scoping the
first one — the bug has to be fixed before tag-all can be built safely, so
it's promoted to its own section and built first, alone.

- §1 (concurrency guard) — built, tested, committed.
- §3 (persona↔model) — confirmed, no code needed.
- §4 (derived status + paused-gating, including the delegation-gating and
  per-persona-resume additions caught in review) — built, tested,
  committed.
- §2 (tag-all / mention-routing / concurrent fan-out) — scoped in full
  below, not yet built. The largest remaining piece; §6's build order
  explains why it's last.

Origin, verbatim: *"i create a group, send a message like 'hey guys, let's
chat about tech' what will happen? then i tag all... also is a persona
limited to a model or even a model can fill all personas. also the online
tag or banner is not dynamic, its still a placeholder."*

---

## 0. What happens today, concretely

Send "hey guys, let's chat about tech" to a 3-persona group right now:
`send_message` reads `conversation.persona_ids[0]` and runs **only that
persona's** turn (`web_adapter/api.py`'s own docstring calls this "judgment
call 3: the lead/entry persona"). The other two personas never see the
message, never respond, and there is no `@mention` syntax anywhere in the
codebase that would change that — the only existing `@mention`-shaped code
is Discord's `_MENTION_RE`/`strip_bot_mention()`, which detects a human
mentioning the *bot itself*, unrelated to addressing other personas.

## 1. The concurrency bug (fix first, alone, before tag-all)

### 1.1 What's actually broken

`send_message` calls `graph_build.new_state(conversation_id, persona_ids[0])`
and spawns a turn **unconditionally** — no check for whether that
conversation's LangGraph thread already has a turn in flight or paused at an
approval `interrupt()`. Proven directly against a real `build_graph()`
(only `call_model` mocked, nothing about LangGraph assumed):

- Persona A pauses at a real approval `interrupt()` (a proposed `file_editor`
  call). A fresh `new_state()` + turn for persona B *on the same thread*
  silently overwrites the checkpoint — `pending_tool_call` → `None`,
  `interrupts` → `[]`, no exception.
- Same clobber happens for **the same persona** sending two ordinary
  messages back to back — this needs no tag-all, no group, no second
  persona. Any DM, today, if a second message arrives while the first is
  still paused on an approval.
- Resuming the now-orphaned original approval via `Command(resume=True)`
  afterward does **not** error — it silently returns whatever the second,
  unrelated turn already finished to. A human clicking "Approve" on a
  prompt that's still on screen gets a result that has nothing to do with
  what they approved.

Root cause: LangGraph's `graph.ainvoke(input, config)` against an existing
`thread_id` *replaces* the keys `input` sets rather than merging — the same
checkpoint-replace behavior already bit this codebase twice this cycle
(cross-turn memory loss, the once-override wipe). `new_state()` always sets
`persona_id`, `pending_tool_call`, etc., so it always wins the replace,
regardless of what was checkpointed.

This is **not specific to tag-all** — it's reachable today, in ordinary
single-persona DM use, with zero of the features below involved. It's listed
here because tag-all is what surfaced it (tag-all necessarily means more
than one turn touching one conversation's thread), and because tag-all
cannot be scoped safely until this is decided.

### 1.2 The fix

A dedicated, non-throwaway option was checked and rejected first: giving
each `(conversation, persona)` pair its own `thread_id`. Rejected for two
concrete reasons, not just complexity:

1. **It orphans every conversation currently paused at an approval** —
   `thread_id` *is* the checkpoint key; re-keying strands the interrupt at
   the old key with no migration path.
2. **It contradicts delegation.** `_handle_delegate` already changes
   `state["persona_id"]` *within one thread* by design
   (`core/delegation.py`) — per-persona threading asserts thread identity
   *is* persona identity, which is the opposite assumption. If Rex delegates
   to Vex and Vex pauses on an approval, "which thread resumes that?" has no
   clean answer under that model.

So the fix keeps one thread per conversation and makes the code that spawns
turns aware of it, mirroring the pattern `_resume_with_answer` already uses
(`aget_state(config).interrupts`) but generalized to cover a turn that's
*running*, not just one *paused*:

- **New shared primitive**, `core/events.py`:
  `find_open_turn(events: list[TapestryEvent]) -> TapestryEvent | None` —
  the exact scan `close_orphaned_turns` and
  `delegation._events_since_current_turn_start` each already hand-roll
  (turn/start with no matching turn/end). Extracting it removes a third
  copy of the same logic rather than adding one; both existing call sites
  get refactored onto it, behavior unchanged.
- **New guard**, needed at two of the three turn-spawning call sites —
  `send_message` (`web_adapter/api.py`) and Discord's `on_message`
  (`discord_adapter/bot.py:461`) — call `find_open_turns(read_events
  (conversation_id))` before `new_state()` + spawn. If non-empty, don't
  spawn: web returns `409` naming the persona and whether it's running or
  awaiting approval; Discord replies in-channel with the same, rather than
  silently dropping the message or corrupting state.
  **Telegram needs no change — checked past the grep, not just at it.**
  The initial pass here found `new_state()` at `telegram_adapter/bot.py:586`
  via grep and assumed the same unconditional pattern as the other two;
  reading the surrounding code instead shows `on_message` already runs
  inside a real `async with self._get_lock(conversation_id)` (a per-
  conversation `asyncio.Lock`, held for the full `drive_graph` await —
  covers a turn that's actively running, not just paused) *and* an
  explicit `snapshot.interrupts` check before touching `new_state()` at all
  (covers a turn already paused at an approval, once the lock's released).
  Between the two, Telegram already can't hit this bug. Worth recording
  precisely because it's the opposite mistake this whole doc exists to
  avoid — trusting a grep match instead of the code path it's inside.
- **Explicitly not building for v1**: queueing the rejected message so it
  auto-sends once the turn clears. Simpler and honest first; can follow if
  the 409 UX chafes in practice.
- **One addition required by §2 below**: `turn/start`'s payload gets a new
  `graph_thread_id` field (distinct from the pre-existing, unrelated
  `thread_id` field already on events — that one is the human-facing
  reply-threading concept read by `core.conversations.derive_messages`;
  conflating the two would be a real bug, not just a naming clash).
  `graph_thread_id` records which LangGraph checkpoint thread a turn ran
  on: `conversation_id` for an ordinary turn, or a fan-out leg's own thread
  id (§2.2) for a tag-all reply. Source it from LangGraph's own
  `RunnableConfig` (`config["configurable"]["thread_id"]`) rather than a
  second, hand-threaded field on `TapestryGraphState` — that can't drift
  from the actual checkpoint key, which a parallel field could. None of the
  three node functions currently declare a `config` parameter (checked:
  `persona_node`, `approval_node`, `execute_node` all take only `state`
  today) — LangGraph's own convention is that a node optionally receives
  `config` as a second argument if its signature declares one, but that's
  new to this codebase, so **verify it directly** (a real node invocation
  asserting the injected `config` matches the `thread_id` passed into
  `.astream()`) before relying on it, rather than assuming the convention
  holds here untested. `find_open_turn`'s guard here filters to
  `graph_thread_id == conversation_id` (or absent, for turns recorded
  before this field existed) — an open fan-out leg must never block a
  plain message on the conversation's main thread.
- **`close_orphaned_turns` needs the same filter, for a different reason —
  caught in review.** It still closes *every* unmatched `turn/start` in a
  conversation on the next post-restart touch. Left as-is, a restart while
  five fan-out legs sit paused at real approvals writes five synthetic
  `turn/end` events while their LangGraph checkpoints still hold live,
  resumable interrupts — the log says done, `list_open_turns()` says idle,
  and the human's pending approvals are unresolvable ghosts. Fix: skip any
  `turn/start` carrying a `graph_thread_id != conversation_id` in the
  blind log-only scan (main-thread repair is unchanged — today, before
  fan-out exists, every turn implicitly has `graph_thread_id ==
  conversation_id`, so this is backward compatible). Fan-out legs get
  reconciled separately and more carefully: before treating one as
  orphaned, check its *own* checkpoint via `aget_state` — LangGraph, not
  the event log, is the actual source of truth for whether that specific
  thread still has something live pending — and only synthesize its
  `turn/end` once the checkpoint itself confirms there's nothing left to
  resume.

### 1.3 Tests

- Unit: `find_open_turn` — empty log, closed turn, open turn, multiple
  closed + one open, delegation-nested turns.
- Regression, real graph (no mocking beyond `call_model`, matching this
  session's existing discipline): reproduce the exact clobber above, assert
  it now raises/409s instead of silently overwriting; assert the *original*
  approval is still resumable and resumes correctly afterward.
- One test per adapter (web/discord/telegram) asserting the guard is
  actually wired at that call site, not just that the primitive exists.

---

## 2. Tag-all / mention-routing

Builds directly on §1's guard — the guard *is* the sequencing mechanism, not
a separate thing bolted alongside it.

### 2.1 Syntax and resolution

- `@all` → every persona in `conversation.persona_ids`.
- `@<persona-id-or-name>` → that persona specifically; multiple explicit
  mentions run in the order they appear in the text, de-duplicated.
  Matched via a word-boundary token (`@\w+`) against persona id first, then
  display name, case-insensitively — verified against all four seed
  personas' YAML (`id`/`name` are single words: `ada`/`Ada`, `rex`/`Rex`,
  `vex`/`Vex`, `nova`/`Nova`), so this covers today's roster exactly.
  Nothing in the schema stops a future persona's `name` containing a space
  (e.g. "Dr. Watson"), which `@\w+` can't match past the first word —
  stated as a known limitation rather than silently mismatched later:
  multi-word display names are only reliably taggable by id.
- **No mention at all → today's behavior, unchanged**: `persona_ids[0]`
  only. This keeps every existing DM and every group message a user has
  already sent behaving exactly as it does today — mentions are additive,
  matching this codebase's established convention (every mode/model feature
  landed so far defaults to current behavior).
- **A resolved mention list containing a paused persona → skip that one,
  fan out to the rest, report which were skipped — never reject the whole
  send.** Unspecified in the first pass of this doc; flagged in review as a
  real gap an implementer would otherwise have to guess at, and §4's gating
  language ("reject a new turn... for a paused persona") reads like it
  could mean rejecting the whole message. It doesn't: on a chat platform,
  `@all` in a 10-persona group where 3 happen to be paused should still
  reach the other 7 — failing the entire send over personas the human may
  not even know are paused is exactly the kind of surprising, all-or-
  nothing behavior a real chat app avoids. `send_message`'s response for a
  multi-persona fan-out includes which tagged personas were skipped and
  why, so the human sees it rather than silently getting fewer replies than
  expected.

### 2.2 Execution: concurrent fan-out, not sequential

**Revised after the first pass of this doc.** Sequential-on-one-thread was
the original design — reusing §1's guard as the scheduler. Wrong call once
sized against how this is actually meant to be used: a *chat platform*,
where most messages aren't about code and don't touch a mutating tool at
all, and a tagged group can be large. Serializing 100 personas behind one
another's LLM round-trips means a "hey guys" message takes minutes to
finish delivering, and one persona's rare approval pause would stall
everyone behind it for no reason connected to them at all. That's not what
`@all` means on any real chat platform (Discord's `@everyone`, similarly, is
delivered to everyone at once — the *access* to ping a large group is what's
gated, never the delivery order).

So: each persona in the resolved mention list gets its own **fan-out
thread**, run concurrently, all from one `send_message` call —
`thread_id = f"{conversation_id}::mention::{persona_id}::{trigger_message_id}"`,
fresh per tag-all message (never reused across sends, so there is no
second-message-on-this-thread case to guard against — each fan-out thread
is single-use by construction). `_drive_turn`/`_resume_with_answer` both
gain a `graph_thread_id` parameter (currently hard-coded to `conversation_id`
in both) so they can run against a fan-out thread instead of the main one.

**Every tagged persona uses a fan-out thread, including the lead
(`persona_ids[0]`) if it's among those tagged** — not "everyone except the
lead, who keeps using the main thread." Giving the lead different
concurrency semantics than the rest of an `@all` would be a real
inconsistency (its replies could block/be blocked by unrelated main-thread
activity that nobody else's replies are exposed to) for no benefit. The
main thread stays reserved for: undecorated messages (today's unchanged
behavior) and delegation's within-turn persona hand-offs.

This is the actual point of the redesign: a rare mutating tool call from
persona 7 of 100 pauses *only leg 7*. The other 99 are unaffected — no
serialization, no stall, no product-facing concept of "waiting your turn"
that a chat platform's users would find alien.

### 2.3 Delegation inside a fan-out leg

`core.delegation.delegate()`'s round cap works by re-deriving "the
currently open turn" from the conversation's event log
(`_events_since_current_turn_start`, `core/delegation.py`) — it scans for
*any* still-open `turn/start` and takes the most recently opened one.
That's a positional guess that assumed at most one turn is ever open per
conversation at a time. Concurrent fan-out breaks the assumption directly:
with legs A, B, C all open at once, leg B calling `delegate()` could get
sliced from whichever leg happens to have the highest event-log index, not
necessarily its own.

Fix is narrow, not a redesign: `_handle_delegate` (`graph/build.py:1097`)
already has its own `turn_id` in scope at the call site
(`_handle_delegate(state, persona, turn_id, arguments, new_messages)`) —
thread it through to `delegate()` explicitly, and change
`_events_since_current_turn_start` to slice from *that specific* turn_id's
own index rather than scanning for "whichever turn is open." Unambiguous
regardless of how many other legs are concurrently open. No behavior change
for the non-fan-out case (there's only ever one open turn there anyway, so
"the current one" and "the most recently opened one" already coincide).

### 2.4 Approval routing across concurrent legs

`_resume_with_answer` and the `ask/requested` frame it responds to both
currently assume one pending interrupt per conversation, addressed purely
by `conversation_id`. With N legs able to pause independently, both need to
carry *which* thread's ask this is:

- The `ask/requested` frame (`_drive_turn`) adds `graph_thread_id` (and the
  persona id, for display) alongside the existing `payload` — the frontend
  needs it to show "Rex is waiting on your approval" per-leg rather than
  one undifferentiated prompt, and to send it back.
- The answer endpoint/`_resume_with_answer` takes that `graph_thread_id` and
  builds `config` from it instead of assuming `conversation_id` — resolving
  leg 7's approval must resume leg 7's checkpoint, not the main thread's
  (which may have nothing pending at all).
- Backward compatible: an ordinary (non-fan-out) ask omits/defaults
  `graph_thread_id` to `conversation_id`, so today's single-persona
  approval flow (and its existing frontend code) is unaffected until the
  frontend is updated to handle multiple concurrent asks.

### 2.5 At scale — cost and blast radius, not just correctness

Asked for directly, so it's a first-class part of this scope, not a
footnote: tagging all of a 100-persona group is 100 concurrent LLM
completions from one human message. Three concrete controls, mirroring how
real chat platforms gate their own `@everyone`-shaped features (access and
rate, not delivery order):

- **A concurrency cap on the fan-out itself** — bound simultaneous
  completions with a semaphore (default 10) rather than firing all N at
  once, regardless of how many are tagged. Keeps provider rate limits and
  cost bursts predictable; personas complete in waves instead of a
  thundering herd. Tunable, not hardcoded to one deployment's provider
  limits.
- **A confirmation step above a small threshold** (default: >5 tagged
  personas) — `send_message` returns a distinct response (or the frontend
  pre-flight-checks the resolved count) asking the human to confirm before
  actually spawning the fan-out. Below the threshold, no friction — this
  keeps the common case (a 2-4 persona group, tagged casually) exactly as
  fast and frictionless as sending any other message.
- **A hard upper cap** (default: 50) past which `@all` is rejected outright
  with a clear error, rather than silently truncating the mention list or
  degrading into something the human didn't ask for. All three numbers are
  config, not load-bearing constants — stated here as sensible defaults to
  build against, not a claim about what's correct for every deployment.

To answer the user's own example plainly: with these defaults, `@all` in a
100-persona group is **rejected outright** with a clear error (100 > the
50-persona hard cap) — not silently fanned out, not silently truncated to
the first 50. Someone would need to either raise the cap deliberately for
that deployment, or tag a smaller, explicit subset instead of `@all`.

### 2.6 Tests

- Mention parsing: `@all` in a 3-persona group, explicit `@rex @vex`
  ordering, no mention (unchanged behavior), unknown handle (ignored, rest
  still resolved — a typo shouldn't fail the whole send).
- Real-graph concurrency test: 3 personas tagged, one hits `file_editor`
  (needs approval, i.e. its own leg pauses at an `interrupt()` — distinct
  from `status == "paused"` below) — assert the other two complete normally
  and independently, on their own threads, without waiting on or being
  blocked by the interrupted one; assert the interrupted leg's checkpoint
  is addressable and resumable via its own `graph_thread_id` afterward.
- Delegation-under-fan-out test: two legs open concurrently, one of them
  calls `delegate()` — assert its round-cap slice is scoped to its own
  turn, not contaminated by the other open leg's events.
- Paused-persona-in-a-mention-list test (§2.1's skip rule): `@all` resolves
  to 5, 2 of them have `status == "paused"` — assert exactly 3 fan-out
  turns are spawned, the send still succeeds, and the response names the 2
  skipped personas rather than silently returning fewer replies.
- Scale controls: mention count at/under/over the confirm threshold and the
  hard cap, each asserted against the actual documented default.

---

## 3. Persona ↔ model — confirmed, no fix needed

`Persona.model` (`core/personas.py`) is a plain string field. No uniqueness
constraint exists anywhere — not in the pydantic model, not in `schema.sql`,
not in any validation path in `web_adapter/api.py`'s create/update persona
handlers. Multiple personas already can, and in the seed set already do
(nothing stops two personas naming the same LiteLLM model string), point at
one model with zero conflict. This was a suspicion to confirm, not a bug —
documented here, no code changes.

---

## 4. Live status — derive it, don't write it

### 4.1 Why not write it

The only existing writer is `pause-all` (`persona.model_copy(update={"status": "paused"})`,
persisted straight to the persona's YAML). Writing `status: "busy"` the same
way at `turn/start` was the first idea and is wrong: a crash mid-turn leaves
that YAML saying "busy" forever (this codebase's own crash-recovery model
is "close by synthetic event," never "mutate the record" — see
`ORPHAN_REPAIR_REASON`), and YAML round-tripping has already been observed
in this project to lose hand-written comments on unrelated fields. Status
must be a **projection**, computed at read time, same invariant this whole
event-sourced core already holds for messages and timelines.

### 4.2 The derivation

New primitive, `core/events.py`: `list_open_turns() -> dict[str, TapestryEvent]`
(persona_id → its open `turn/start` event), a real unbounded scan across all
conversations — **deliberately not** `read_recent_events(limit=...)`, which
is cross-conversation but window-bounded and could miss (or misreport) an
old crashed turn sitting in a quiet conversation depending on how much
activity happened elsewhere in the meantime. `turn/start`'s `actor` field is
already the persona id (`graph/build.py:896`), so this is a straight
group-by, no schema change. With §2's concurrent fan-out, one conversation
can legitimately have several different personas' `turn/start` open at
once (one per active fan-out leg) — `list_open_turns()` is unfiltered by
`graph_thread_id` on purpose, since "is this persona busy" should be true
regardless of which thread it's busy on. Worth stating plainly so a future
reader doesn't mistake several concurrent open turns in one conversation
for the bug §1 fixes — it's the intended shape now.

**Call `list_open_turns()` once per request, not once per persona — caught
in review.** `_persona_to_out` runs once per persona on every roster/list
render; naively calling an unbounded whole-table scan from inside it means
a 4-persona roster does 4 full scans, and the 100-persona case §2.5 scopes
would do 100. Compute the open-turns map once at the top of whichever list
endpoint is rendering a roster, and pass it into `_persona_to_out` as a
parameter — same data, one scan.

`_persona_to_out` (every read path: conversation roster, persona list, DM
header — everywhere a persona is projected) computes, in this order:

1. YAML `status == "paused"` → `"paused"` (an explicit human action wins,
   always).
2. Else persona id present in `list_open_turns()` → `"busy"`.
3. Else the YAML value as today (in practice `"online"`/`"offline"` for
   every seed persona).

Known limitation, stated rather than silently accepted: mid-turn delegation
(Rex → Vex) doesn't change who `turn/start`'s `actor` is, so a delegated-to
persona doesn't independently show `"busy"` — the *turn owner* does. Fine
for v1 (delegation legs are short); flagged here so it isn't mistaken for
an oversight later.

### 4.3 The other half: it has to actually gate something

Grepped and confirmed: **nothing in `graph/build.py` or `web_adapter/api.py`
reads `persona.status` when deciding whether to run a turn.** Nova's
YAML-default `paused` status today is purely cosmetic — messaging her runs
her exactly like any other persona. That's a second real gap under the same
"placeholder" complaint, and it's a judgment call below: should `paused`
actually block a persona from running (making "Pause all agents" a real
safety control), or stay display-only? There's also no unpause/resume-all
endpoint today at all — the only way back to `"online"` is editing the
persona (already possible via the existing edit form / `draft.status`);
this scope doesn't add one unless the answer below implies it's needed.

**Gating the front door isn't enough by itself — caught in review.**
`send_message` and its Discord/Telegram equivalents are the only places
named above, but they're not the only way a persona's turn runs: delegation
already runs an arbitrary target persona mid-turn
(`_handle_delegate`/`core.delegation.delegate()`), with none of these three
call sites involved at all. Gate only the front door and Nova is still
reachable exactly the way her system prompt says she must never be —
through Rex delegating to her, an agent granting the "standing
authorization to deploy" she's designed to require a human for. `delegate()`
(or `_handle_delegate`, before it calls `delegate()`) needs the same
paused check: delegating *to* a paused persona fails that tool call with a
message the delegating persona's own turn sees, rather than silently
running the target.

### 4.4 Tests

- `list_open_turns` unit tests: no open turns, one, one per persona, one
  stale one from a different (never-revisited) conversation still found.
- `_persona_to_out` derivation: paused-in-YAML wins over an open turn;
  open turn → busy; neither → YAML value passes through unchanged.
- Gating: a turn-spawn attempt (main-thread or fan-out leg) at a paused
  persona is rejected with a clear error, not silently run.
- `POST /api/agents/{persona_id}/resume`: flips exactly that persona to
  `"online"`, leaves every other persona's status untouched (this is the
  test that most directly proves Nova stays gated for everyone else while
  becoming reachable once a human explicitly resumes her specifically).
- Delegation gating (see §4.3): Rex delegating to a paused Nova fails the
  delegate call with a message Rex's own turn can see, rather than running
  her — asserted with a real graph, not mocked at the delegate() boundary.
- Frontend: `StatusDot`/`StatusPill` already render whatever `status` they're
  given (existing tests cover that); the gap here is purely that the value
  reaching them is now dynamic — no new frontend logic needed, confirmed by
  reading `web/components/persona/StatusDot.tsx`. Live re-fetch/animation on
  the frontend (so a dot actually flips in an open tab without a manual
  refresh) is a separate, smaller follow-up noted here, not solved by this
  backend change alone.

---

## 5. Decisions recorded

Two real product forks were asked directly rather than decided solo,
matching how modes/models switching was scoped:

1. **Tag-all mid-sequence stall** → the chosen answer was "queue, auto-
   continue after resolve," and §2.2's concurrent redesign satisfies that
   intent by construction rather than replacing it: resolving a paused
   leg's approval resumes *that leg* automatically (§2.4), and no other
   tagged persona was ever blocked on it in the first place. Nothing is
   dropped or deferred — every tagged persona still gets a turn; the fix
   just removes the reason any of them would have needed to wait.
2. **Paused-persona gating** → confirmed: `status == "paused"` should
   actually block a persona from running, not just display as paused.
   `send_message` (and the Discord/Telegram equivalents) reject a new turn
   — main-thread *or* a fan-out leg — for a paused persona with a clear
   error/reply, making "Pause all agents" a real control.

   **This cannot ship alone — caught in review.** There is currently no
   unpause path anywhere: no per-persona resume endpoint, no resume-all,
   and `PersonaEditForm` doesn't even expose a `status` control (confirmed
   by reading it — the one `status` reference there is an unrelated
   new-persona-draft default). `nova.yaml` ships `status: paused`
   *deliberately* — her own system prompt: "You start paused by default and
   must be explicitly activated by a human before taking any action." That
   default is correct and stays. But gating turn a persona can never be
   unpaused into is a worse bug than the placeholder dot this whole doc set
   out to fix — it bricks Nova out of the box and turns "Pause all agents"
   into a one-way door for everyone the moment anyone clicks it. Gating and
   an unpause path ship in the same change:
   - `POST /api/agents/{persona_id}/resume` — sets that one persona's
     status to `"online"`. This is what makes Nova's own documented design
     actually usable: a human explicitly activating *her*, specifically.
   - **No blanket `resume-all` in this pass — reconsidered during review.**
     The first draft of this doc proposed one "since invoking a blanket
     resume is itself a conscious human action," but that doesn't survive
     contact with what pause-all → resume-all actually means for Nova: a
     human hitting pause-all to say "stop my chat agents for a minute" and
     then resume-all to undo it would silently reactivate Nova too — the
     exact standing authorization her prompt forbids — even though
     resuming *her* specifically was never the intent either time. Building
     `resume-all` correctly would mean `pause-all` recording which personas
     it flipped so resume only restores those — real added scope for a
     control nobody asked for yet. Dropped for now: per-persona resume is
     the one primitive actually required to un-brick anything, and for a
     4-persona roster that's 4 clicks, not a missing convenience.

## 6. Build order

1. §1 alone: the open-turn guard on the main thread, `find_open_turn`,
   refactor the two existing duplicate scans onto it, tests. Ships
   independently — fixes a real bug reachable today with none of the rest
   of this doc built yet.
2. §4: derived status (`list_open_turns`, `_persona_to_out` precedence) plus
   paused-gating from the decision above. Independent of §2 — can land
   right after §1.
3. §2: mention parsing, fan-out threads, delegation round-cap fix, approval
   routing, scale controls — the largest piece, built last since it depends
   on §1's `graph_thread_id` field existing first.

Each lands with its own tests passing before the next starts, same
verify-before-trusting discipline as every fix earlier this session: write
the fix, write the test, confirm it fails without the fix and passes with
it, run the full suite, commit in focused increments.
