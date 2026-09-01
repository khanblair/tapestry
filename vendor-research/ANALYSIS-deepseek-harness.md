# DeepSeek Harness (dsh) — Architecture Research for Tapestry

Repo: `github.com/deepseek-ai/deepseek-harness`, cloned to `vendor-research/deepseek-harness`. Published by DeepSeek AI, 2026-08-13, MIT licensed (confirmed, root `LICENSE`, no caveats).

## Summary

`deepseek-harness` is a large (8,846-file), mature TypeScript monorepo built on **Cordis**, a pre-existing third-party plugin/DI framework (not authored by DeepSeek) that the project vendors into its own source tree. The "everything is a plugin" claim is real but more modest than it first sounds: there is exactly **one** concrete agent-loop implementation shipped (a ReAct-style act→observe driver literally named `ReactLoopAgent`), and "the loop is a plugin" means you can replace that whole class by implementing a ~12-method interface and registering it — not that the harness ships multiple selectable loop *strategies* (no plan-then-execute variant, no built-in reflection/self-verification step). **Skills**, by contrast, are a first-class, well-designed subsystem that is structurally very close to Claude Code's own Skill system: `SKILL.md` files with YAML frontmatter, kebab-case names, layered filesystem discovery by rank, a model-facing catalog (name+description only) injected as context, and a `skill({name})` tool that loads the full body on demand. The tool registry is a clean, portable design (JSON-Schema DSL, guarded pipeline with allow/deny/ask policy, per-scope restrictions, concurrency-safety flags). The model adapter is a lower-level, more opinionated abstraction than LiteLLM (raw stream-chunk protocol, adapter-owned replay state, canonical error codes) — it doesn't replace LiteLLM's job so much as illustrate stricter adapter *contracts* worth imposing on top of LiteLLM. The session log is a genuinely well-thought-out event-sourced design (append-only, projection function, crash recovery via synthetic close-out, checkpoint batching) that validates Tapestry's own event-sourcing choice and adds concrete techniques worth copying. The biggest transferable ideas are **interface/pattern-level**, not code: a provider-neutral "ask a human" seam, a content-hash technique for keeping a mutating catalog coherent inside an append-only log, and a layered (not monolithic) approach to budgets. The biggest thing *not* to imitate is the sheer organizational cost of the plugin/DI machinery itself, which is justified there by third-party plugin distribution — a problem Tapestry likely doesn't have.

---

## 1. What is "Cordis"?

**Cordis is a separate, pre-existing open-source project, not something DeepSeek invented.** It is vendored (source-copied) into the repo rather than depended on via npm.

- `README.md`: built on an "everything-is-a-plugin" architecture and powered by Cordis (`github.com/cordiverse/cordis`).
- `vendor/README.md`: source-vendored copies of Cordis and its foundation libraries, copied in "so that the harness fully owns its framework layer (auditable, patchable, pinned)." Manifest: `cordis/` → npm name `@deepseek-ai/cordis`, upstream `cordis` v4.0.0-rc.7, from `cordiverse/cordis` (packages/core) — historically the plugin framework behind the Koishi chatbot project. Sibling vendored packages (`cosmokit`, `schemastery`, plus Cordis plugins `loader`/`include`/`group`/`timer`/`hmr`/`logger-console`) come from the same upstream ecosystem.
- Renamed into the `@deepseek-ai` npm scope purely to avoid squatting the upstream names on the registry. A "Local modifications" log (19 numbered entries) documents DeepSeek's real patches on top of upstream Cordis (lifecycle-race hardening, JSDoc enrichment, transactional config reconciliation) — genuine engineering, but on top of someone else's framework.

**What Cordis provides**, in its own words ("Cordis In Five Ideas," `docs/cordis-primer.md`):
1. A plugin implements `Service` — a function with `inject`/`apply(ctx)`, or a `Service` subclass.
2. A context (`ctx`) is a **dependency-injection repository of services** — plugins claim a stable `ctx.<key>`; consumers resolve by key, never by importing the concrete implementation.
3. `inject` declares service dependencies, so a plugin waits until its dependencies exist.
4. **Typed events** for communication, in five dispatch modes: `emit` (fire-and-forget), `waterfall` (around-middleware, must call `next()`), `parallel`, `serial`, `bail` (first non-undefined wins) — a typed, multi-mode event bus.
5. Registrations are **reversible effects** (`ctx.effect()`/`ctx.on()`), so unload/reload unwinds cleanly (hot-module-reload support is first-class).

Cordis is simultaneously a DI container, a plugin lifecycle/effect system, and a typed event bus — one unified "plugin tree" runtime, not three bolted-together concerns.

---

## 2. "The agent loop itself is a plugin" — the actual contract

**The plugin contract is `AgentFactory` + the `Agent` interface, not a rich "loop strategy" interface.**

`packages/core/agent/src/index.ts:176-193` (`AgentFactory`):
```ts
export interface AgentFactory {
  createAgent(ownerCtx: Context, options: CreateAgentOptions): Promise<AgentHandle>
  resume(ownerCtx: Context, options: ResumeAgentOptions): Promise<AgentHandle>
}
```
Registration: `ctx.agents.setFactory(factory)`, called once by the loop package at `packages/core/agent-loop/src/index.ts:413`.

Whatever the factory constructs must satisfy the public `Agent` interface (`packages/core/agent/src/types.ts`): `id`, `options`, `session`, `inbox`, `status`, `ctx`, plus `cancel()`, `whenIdle()`, `runMaintenance()`, `send()`, `followup()`, `steer()`, `inject()`. That's the actual "loop plugin" surface — roughly a dozen methods plus inbox/cancellation/quiescence contracts, not a small strategy interface.

**What ships by default is a single, explicitly ReAct-style loop, not a family of strategies.**
- `packages/core/agent-loop/src/agent.ts:70`: `export class ReactLoopAgent implements Agent`.
- `packages/core/agent-loop/README.md`: "It is the harness's **only concrete loop**... swap it by implementing `Agent` and registering through `ctx.agents`."
- No plan-then-execute variant, no reflection/self-verification loop, no config knob to pick between strategies. Grepping the whole tree for other `implements Agent`/`setFactory(` sites found only this one implementation (`packages/experimental/agent-team` is a multi-agent *coordination* layer over several single-agent loops, not an alternative single-agent loop strategy — see Bonus below).
- Turn/step mechanics (`docs/architecture.md`, "Turn flow"): open turn → claim queued input via `agent/pre-step` (waterfall, can reject/rewrite the entering batch) → assemble request via `agent/request` (waterfall, config only) → stream via `llm/stream` → dispatch tools through `tools/pre-execute → tools/execute → tools/post-execute` → close via `agent/turn-stopping` (serial, no veto — only "steer to keep going" or let it close) once nothing is owed. A clean act-observe-repeat ReAct loop with well-named interception points — but the *only* loop pattern shipped.

**Budget-aware stopping is real, but layered — not a single mechanism, and not in the default loop driver.**
- **No per-turn/per-step budget in the loop itself.** `packages/core/agent-loop/README.md`, "Known Limitations": *"No built-in turn budget... a policy that bounds runaway turns must cancel from an existing lifecycle extension point such as `agent/turn-stopping`."*
- **A durable, replay-enforced round budget exists as an optional domain plugin ("goals").** `packages/goal/goal/src/types.ts:67` — `GoalSnapshot.maxGoalRounds`, folded against `roundsStarted` from the session log itself; replay "rejects non-positive rounds, gaps, stale revisions, stopped phases, and cap overflow." A separate service (`ctx.goals`) with explicit `active | paused | blocked | complete` phases and a `block(agent, ref, reason)` call carrying a stable machine-readable code.
- **A persisted recursion-depth budget exists for subagents.** `packages/core/session/src/types.ts` (`SessionHeader.delegationDepth`): parent depth + 1 for a subagent child, "persisted so a recursion budget survives restart and resume — a runtime-only depth would reset a resumed child to top-level."
- **Cost/token pressure is measured, not gated.** `ctx.tokenMeter.measure()` returns an immutable measurement by replaying the log — a pure pricing/observability primitive; nothing stops a turn on its own. A cost-ceiling policy would be built as yet another plugin consuming this measurement.

**"Declare done, but verify first" is genuinely absent, not merely deferred.**
- `docs/subsystems/todo.md`: `TodoItem` is "deliberately minimal" — content line + three-state status, no verification gate before `completed`.
- `docs/subsystems/plan.md`: plan mode is "soft guidance" — the `exit_plan_mode` tool only requires a markdown plan for *human* review, no model-facing self-check.
- `agent/turn-stopping` has no veto and carries no verification semantics — a listener can only steer to keep going, never gate on a check having passed.

**Conclusion:** DSH gives Tapestry a real reference for one loop shape (ReAct with clean interception points) and a real pattern for layered, replay-safe budgets — but no template for reflection or self-verification. Those remain open design problems for Tapestry to solve itself.

---

## 3. Skills — a genuine, well-designed equivalent

Family: `packages/skill/skill` (`ctx.skills`), `packages/skill/skill-filesystem` (local provider), `packages/skill/skill-badge` (packaged provider), `packages/skill/tool-skill` (the model-facing tool).

**File format.** Directory bundles (`<name>/SKILL.md`) or flat files (`<name>.md`). Kebab-case names enforced by regex (`packages/skill/skill/src/index.ts:21`). Frontmatter requires `name` + `description`; optional `whenToUse`; invocation-control keys `disable-model-invocation` and `user-invocable` (boolean-ish, default permissive). Legacy camelCase key names are explicitly rejected with a migration-pointing error — fail loud instead of silently accepting the deprecated spelling.

**Discovery — filesystem convention with a rank-ordered search path:**

| Rank | Source | Root |
|---|---|---|
| 100 | project-dsh | `<projectRoot>/.dsh/skills` |
| 200 | project-agents | `<projectRoot>/.agents/skills` |
| 300 | custom | `Config.customSkillDirs` |
| 400 | user-dsh | `<dshHome>/skills` |
| 500 | user-agents | `<agentsHome>/skills` |
| 600 | bundled | `Config.bundledSkillDir` |

`projectRoot` is the nearest ancestor containing `.git`. Filesystem-watched for live add/remove. Architecturally identical in spirit to Claude Code's own skill lookup path.

**Discoverable, but bodies aren't preloaded.** The registry exposes name + description + `whenToUse` + invocation policy (never the body) as a catalog, injected once per session as a context message. The full body loads only on demand via a `skill` tool call, whose description reads: *"Load the full instructions for an available skill. Call this with the exact skill name... before acting on a task that names or clearly matches that skill."* An explicit **human invocation gesture** also exists — a `/skill-name` token in a raw user message (scanned only in user-authored messages, so untrusted injected text can't forge it) loads a `user-invocable` skill's body directly, no tool round-trip needed.

**Tool vs. skill, in this model:** a tool is always in the model-facing schema (paid in context every step) and is a *capability*. A skill costs one line in a catalog until loaded, and is *knowledge/procedure*, not a capability. Skills are layered exactly like the tool registry (nearest scope, then rank, then provider order).

**The one genuinely novel technique here — directly relevant to Tapestry's event-sourcing:** keeping a mutating catalog coherent inside an append-only log. A SHA-256 digest over the canonical `(name, description)` pairs is compared against the digest embedded in the most recently visible catalog message; a brand-new full-replacement message is appended (never an edit to history) only when the digest changed, with distinct "first time" vs. "catalog changed" framing, and an explicit "no skills available, forget earlier names" message when the catalog empties. **General technique: content-hash a piece of externally-mutable state, and only append a new immutable log event when the hash changes, replacing — never mutating — the model's view of it.** Applicable to any dynamic catalog a persona needs kept coherent across turns (tool availability, memory/RAG index state, etc.), not just skills.

---

## 4. Tool registry — design pattern, transferable

A `ToolDefinition` is a schema (model-facing) plus a mandatory output schema + a pure `render(args, value)` projector, plus host-only metadata (`timeoutMs`, `isConcurrencySafe(args)`, UI presenters) that structurally cannot leak into a model request, enforced by an explicit allowlist rather than convention.

**Guarded execution pipeline — the most portable idea here:** every tool call runs through `tools/pre-execute` (a reorderable **allow/deny/ask waterfall** — where an approval/permission system plugs in), registered guards, `tools/execute` (around-dispatch, where a timeout policy wraps), `tools/post-execute` (inspect/replace result), then an immutable final record. This is directly the shape of a middleware/interceptor chain — needs no TypeScript-specific machinery to reproduce in Python/LangGraph.

**Per-scope tool restriction as data, not code:** `{allow?, deny?}` intersecting up a scope chain while a scope's own registrations stay exempt "so a delegated child keeps the tools it answers through." Directly applicable to giving different Tapestry personas different tool grants (e.g., a read-only reviewer persona vs. a full-access coder persona) without forking code.

**Concurrency safety is opt-in and conservative:** a synchronous, args-only classifier; anything that throws, returns non-`true`, or is omitted is treated as **exclusive** (must run alone). Good default for a system doing real file/shell/git side effects, where two racing tool calls is a correctness bug, not a performance question.

---

## 5. Model adapter — lower-level than LiteLLM, not a replacement

An abstract `LlmAdapter` streams a raw, well-specified chunk protocol; a separate shared assembler folds the stream back into content blocks, so "reassembly is not each adapter's problem."

**What's worth borrowing as policy on top of LiteLLM, not as a replacement for it:**
- A closed, canonical failure taxonomy — context-window-exceeded via a mandatory classifier every adapter implements, consumers route on the code, never provider text.
- **An empty completion is explicitly classified as a retryable error, not a silent success** — a subtle, easy-to-miss correctness bug in naive wrappers, worth adopting as a rule regardless of what sits underneath.
- Disjoint, auditable token accounting (cache reads/writes kept separate from uncached input tokens).
- Adapter-owned "replay state" — a place to hang provider-specific continuation data (e.g., a signed extended-thinking block) without polluting the provider-neutral message model; only the same adapter instance is trusted to reinterpret it later.
- Retry policy lives at the agent-loop level (a named `agent/request-error` event), not hidden inside an HTTP client — a single, observable, overridable extension point instead of scattered try/except around calls.

Not a drop-in replacement — LiteLLM already normalizes the chat-completion/streaming/tool-calling surface across our five providers. What DSH offers is contract discipline to layer above it.

---

## 6. Session log — validates and sharpens Tapestry's own event-sourcing design

Core invariant, stated as a hard rule: *"Model-visible means logged."* History is projected from the append-only log, never stored as a second representation — exactly Tapestry's own design, with DSH as a load-bearing existence proof it works at scale (twelve core event types).

**Concrete techniques worth copying directly:**
- **Crash recovery by synthetic close-out, never truncation.** A log that crashed mid-turn is found with an open start-event and no matching end-event on reload. Rather than truncating (a single turn can be huge in a long-horizon task), the reader closes the orphan with a synthetic end-event carrying a reason value **reserved exclusively for post-hoc repair, never emitted by live code** — so a reader can always distinguish "this ended because the process died" from any live termination reason.
- **Bounded write-batching with a flush checkpoint** — durable-enough-to-be-correct without flushing on every single append, with an explicit checkpoint before the loop claims the next turn.
- **Header/log separation with a hard version gate** — format version stamped once at creation; a persistence backend rejects any other version on load, no migration. A deliberate choice to force breaking changes into an intentional new-session decision rather than a runtime migration risk.
- **The same lock-bracket-in-the-log pattern reappears for compaction** (start → work → summary → end, released last) specifically so a mid-operation crash becomes a *detectable* orphaned lock rather than a false "finished" claim — the same "detect, don't hide" philosophy, worth adopting as a house rule for any long-running resumable operation Tapestry logs.

Does not contradict Tapestry's design — if anything, a stronger, more battle-tested version of the same idea.

---

## 7. License

Confirmed **MIT**, no caveats — root `LICENSE`, "Copyright (c) 2026 DeepSeek," standard MIT text. Third-party licenses (including the vendored Cordis-ecosystem packages) separately disclosed in `THIRD_PARTY_NOTICES.md`, which preserve upstream MIT licenses. No dual-licensing, no field-of-use restriction.

---

## 8. Language-agnostic pattern vs. irreducibly JS-specific

**Don't try to port the mechanism, only the intent:**
- The `...Map → derived-union` extension mechanism (session events, content blocks, message sources) relies on TypeScript declaration merging — no Python analogue. A Python reimplementation becomes a runtime registry dict, which is fine but trades away compile-time exhaustiveness; compensate with `pydantic` discriminated unions plus an explicit registration-time uniqueness check rather than assuming equivalent safety for free.
- Cordis's fiber/effect disposal machinery is a hand-tuned solution to JS's single-threaded-but-reentrant-via-microtasks model — a different-shaped problem from Python's asyncio/GIL reentrancy hazards, not a port.
- The build/tooling apparatus (pnpm workspaces, dual ESM/CJS vendor packaging, generated doc-catalog tooling) is pure JS-ecosystem plumbing, no bearing on Tapestry.

**Genuinely portable as a documented interface/format:**
- **The `SKILL.md` file format itself** — pure text-file convention, byte-for-byte parseable in Python. Close enough to Claude Code's own shape that adopting this exact frontmatter vocabulary would likely make skill files reusable across Claude Code and Tapestry with zero translation — worth treating as a de facto emerging standard rather than inventing a third dialect.
- The session event vocabulary and its invariants (model-visible-means-logged, derive-never-store, crash-recovery-by-synthetic-close, header/log separation) — data-modeling decisions expressible in any language with an append-only store and a projection function.
- The tool pipeline's phase names and decision shape (pre-execute allow/deny/ask waterfall → execute wrapper → post-execute → immutable result) — a middleware-chain design implementable in any language with function composition.
- **The provider-neutral "ask a human" seam** (see Recommendation) — a request/answer schema with structured options, multi-select, free-text override, and a tagged presentation-intent extension point is pure data-shape design, directly implementable as a Pydantic model with Discord/Telegram/web renderers underneath.
- The content-hash-then-append-if-changed catalog-coherence technique is an algorithm, not a language feature.

---

## Recommendation

### (a) Borrow for Tapestry's loop-engineering design

1. **Adopt the turn/step vocabulary and its named interception points as a design checklist, even without Cordis.** Open turn → claim input → pre-step decide-what-enters → assemble request → decide-config → stream → dispatch tools → post-execute → turn-close decide-whether-more-is-owed. Whatever LangGraph node structure Tapestry builds, name and document the equivalent points explicitly rather than leaving them implicit in graph edges.
2. **Adopt layered, replay-safe budgets, not one global counter.** A durable, replay-enforced cap scoped to an objective; a separate persisted recursion-depth cap for subagent delegation (persisted specifically so a resume can't silently reset it); a separate token/cost measurement service some policy can consult. Three different concerns, kept as three different pieces of state.
3. **Adopt the "detect, don't hide" crash-recovery philosophy** for anything long-running and resumable — a synthetic close-out event with a reason value reserved exclusively for post-hoc repair, applied uniformly to turns, compaction, or any other bracketed multi-step operation Tapestry logs.
4. **Build the self-verification/"declare done" step ourselves — DSH gives us nothing here.** Correctly identify that as a gap rather than force-fitting turn-stopping or the todo system into that role. This is squarely Tapestry's own differentiator to design, and DSH's explicit absence of it is evidence such a step is *not* a solved problem other harnesses already handle.
5. **Adopt the provider-neutral `ask()` seam wholesale as an interface shape** — arguably the best single finding for a multi-channel product. One `ask_user(questions: [{id, question, detail?, options?, multiSelect?, intent?}]) -> {answers: [...]}` contract, with each channel (Discord, Telegram, web) implementing it as a renderer. Directly matches the scoped spec's own "Approvals across surfaces" section — this is the concrete shape that section was missing.

### (b) Borrow for Tapestry's skills design

1. **Adopt the `SKILL.md` frontmatter format close to verbatim** (`name`, `description`, `whenToUse`, `disable-model-invocation`, `user-invocable`, kebab-case) — buys near-drop-in compatibility with the Claude Code skill ecosystem.
2. **Adopt the two-tier discoverability model**: a cheap, always-injected catalog of name+description only, plus an explicit on-demand load tool call for the full body. Near-zero context cost for unused skills, still discoverable-by-name mid-task.
3. **Adopt the rank-ordered, layered filesystem discovery** so a deployment, a specific workspace, and an individual persona can each contribute or override skills without a central registry file.
4. **Adopt the content-hash-then-replace catalog-coherence technique** for Tapestry's event log generally, not just skills.
5. **Adopt the explicit user-invocation gesture** (`/skill-name` in a raw human message) as a deterministic bypass around model judgment.

### (c) Arguments against borrowing, stated directly

1. **The loop is not "pluggable" in the sense Tapestry actually needs.** DSH ships exactly one loop strategy and frames "pluggable" as "reimplement the whole interface if you want something different" — an escape hatch for total replacement, not a menu of strategies. LangGraph already does the actual thing Tapestry needs better: built from the ground up for graph-shaped, per-persona-configurable control flow. DSH gives no evidence this needs reinventing.
2. **Reflection/self-verification is absent, not deferred to configuration.** Don't let this research get cited later as "DSH handles verification via X" — it doesn't, on any axis checked.
3. **The overall architectural cost is large, and its benefit (third-party plugin distribution) is a need Tapestry may not share.** 8,846 files, ~50 packages, a fully vendored-and-patched third-party framework, exist to make every seam independently replaceable by third parties DSH doesn't control. If Tapestry's actual requirement is "configure a small number of first-party personas with different tool/skill/model combinations," that's a materially smaller problem — a plain Python registry (dict-of-callables plus a couple of ordered hook lists for the pre-step/pre-execute/turn-stopping-equivalent events) gets most of the interception-point value without the plugin-distribution machinery cost.
4. **The model-adapter layer is not something to adopt wholesale over LiteLLM** — it solves a lower-level problem LiteLLM has already solved for our provider set. Adopt its *policies*, not its machinery.
5. **"Everything is a plugin, including the loop" oversells what's actually swappable in practice.** Describe Tapestry's own loop-engineering ambitions in terms of the interception points it actually needs, not in terms of matching DSH's tagline.

### Bonus finding: `agent-team` (experimental, outside the required questions)

An opt-in multi-agent coordination layer built on top of several single-agent loops: a durable roster with lifecycle phases, a durable mailbox with wake/don't-wake delivery semantics and target-side de-duplication, and a shared task DAG with `blockedBy` edges (must stay acyclic) plus *advisory, not enforced* write-scope path prefixes. Since Tapestry's whole premise is multiple personas conversing with each other and a human, this is worth a dedicated follow-up read — the mailbox wake/don't-wake distinction and the advisory-not-enforced write-scope idea both look directly reusable for Tapestry's own persona-to-persona delegation design.

## Key file paths for follow-up reading

- Clone: `vendor-research/deepseek-harness`
- Architecture: `docs/architecture.md`, `docs/cordis-primer.md`
- Loop: `packages/core/agent-loop/src/agent.ts` (`ReactLoopAgent`), `packages/core/agent-loop/README.md`, `packages/core/agent/src/index.ts` (`AgentFactory`)
- Skills: `packages/skill/skill/src/index.ts`, `packages/skill/skill-filesystem/src/index.ts`, `packages/skill/tool-skill/src/index.ts`, `docs/subsystems/skills.md`
- Tools: `packages/core/tools/src/index.ts`, `packages/core/tools/src/schema.ts`, `docs/subsystems/tools.md`
- LLM adapter: `packages/llm/llm/src/index.ts`, `docs/subsystems/llm-streaming.md`
- Session/persistence: `packages/core/session/src/types.ts`, `docs/subsystems/session.md`, `docs/subsystems/persistence.md`
- Budgets/human-in-loop: `packages/goal/goal/src/types.ts`, `docs/subsystems/goal.md`, `docs/subsystems/token-meter.md`, `packages/interaction/user-questions/src/index.ts`, `docs/subsystems/user-questions.md`
- Agent teams (bonus): `packages/experimental/agent-team/src/types.ts`, `docs/subsystems/agent-team.md`
- License/provenance: `LICENSE`, `vendor/README.md`, `THIRD_PARTY_NOTICES.md`
