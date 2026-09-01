# Tapestry — Scoped Spec (v1)

## What this is

A self-hosted, multi-agent workspace: named AI personas that a human can talk to one-on-one or in group conversations, coordinating with each other by delegation, working on real code with real tool access, across Discord, Telegram, and a web app. Built on existing open-source primitives for the hard, already-solved infrastructure (model access, checkpointed execution, tool implementations) — but the orchestration, persona model, and chat surfaces are ours to build and own.

This is the scope Tapestry actually ships now, distilled from a longer original vision after discovering how much of it maps onto existing pieces we can compose instead of writing from scratch.

## Architecture

**One platform-agnostic core, thin adapters per surface.**

The core owns: persona/identity registry, delegation routing, the event log, tool execution, and permission gating. Discord, Telegram, and the web app are each a thin adapter translating the core's events into that platform's native shapes and back.

A conversation lives in the core and *projects* onto whichever surface(s) it's bound to — a message in a Discord channel can also appear in the web UI if that conversation is bound to both. Personas, permissions, and history are shared; the surface is just where a human happens to be looking.

## Engine layer (Python backend)

- **LangGraph** (MIT) — the orchestration graph: what step runs next, checkpointed state, and `interrupt()` for pause/resume. This is how approve/reject/stop actually works — pause execution, surface the decision, resume with the answer, state intact. We define the graph; LangGraph gives us durable pause/resume instead of hand-rolling checkpoint serialization.
  - Note: `langgraph` itself is MIT and free to embed. `langgraph-api` (their hosted server product) is Elastic License 2.0 — we don't use it.
- **LiteLLM** — normalizes model providers (Claude, DeepSeek, Gemini, Qwen, OpenRouter, local models) behind one interface. Called from inside LangGraph nodes. Wrapped in a thin policy layer, not used raw: an empty completion is treated as a retryable failure, not a silent success, and context-window-exceeded is classified into one canonical code rather than matched against provider error text at each call site.
- **openhands-tools** (MIT) — `FileEditorTool` and `TerminalTool` behind a thin shim, rather than hand-writing file-edit and shell-execution correctness ourselves.
- **metamcp** — anything not covered above (web search, browser, git, etc.) comes in as MCP tools via the aggregator already running.

## Persona model

A persona is a scoped identity: its own model choice, its own tool/MCP permissions, its own standing instructions (a system prompt). Delegation between personas is a typed message with retry semantics. Group conversations get a hard round cap so agent-to-agent mentions can't spiral into a loop.

(Design borrowed conceptually from Hermes Agent's Bot Mode — not its code.)

## Loop engineering

How a persona actually iterates, not just what it's allowed to do. Verified against DeepSeek Harness's own agent-loop design (MIT, real source, see `vendor-research/ANALYSIS-deepseek-harness.md`) — adopting its interception-point vocabulary and budget layering, explicitly *not* its "the whole loop is a swappable plugin" framing, which turned out to ship exactly one loop strategy and isn't the problem LangGraph already solves better for us.

- **Named interception points**, mapped onto LangGraph nodes rather than left implicit in graph edges: what enters a turn, what the model-call config is, how a tool dispatch is gated, whether the turn is actually done. Same shape as the persona/approval/execute node split already required by `interrupt()`'s re-execution behavior.
- **Layered budgets, not one counter**: a durable round-cap per task, a *separately persisted* recursion-depth cap for persona-to-persona delegation (persisted specifically so a resumed subagent can't silently reset it), and a token/cost measurement that informs a policy rather than gating by itself.
- **Self-verification before declaring done is ours to build — nobody we researched has solved this.** A persona-configurable pre-completion check (re-read the diff, re-run the tests, restate the ask) before a task can close. This is a real gap in every harness surveyed, not integration work.
- **Crash recovery by synthetic close-out, never truncation**: a turn that crashes mid-flight gets closed by a repair event carrying a reason code reserved exclusively for post-hoc recovery — so a reader can always tell "this ended because the process died" apart from any real stopping decision.

## Skills

A skill is packaged, reusable procedure — not a tool. A tool is a capability the model calls to do something; a skill is knowledge the model reads and follows, invoked by name instead of re-derived from scratch every time (e.g., "debug systematically," "do TDD"). Format adopted close to verbatim from DeepSeek Harness, which is itself close enough to Claude Code's own skill shape that files should be usable across both with no translation:

- `SKILL.md` with YAML frontmatter — `name`, `description`, optional `whenToUse`, and `disable-model-invocation`/`user-invocable` flags.
- Two-tier discovery: a cheap catalog (name + description only) is always in context; the full body loads only on demand through one tool call — near-zero cost for skills that never get used.
- Rank-ordered lookup — project-level skills override user-level override bundled defaults, so one workspace or one persona can add or shadow skills without touching a central registry.
- An explicit `/skill-name` gesture in a human's own message force-loads a skill, bypassing model judgment — a predictable escape hatch.
- The catalog is kept coherent inside the event log by content-hashing it and appending a full-replacement event only when it changes — never editing history. The same technique applies to any dynamic state a persona needs kept coherent across turns, not just skills.

## Chat surfaces (all three in v1)

- **Discord** (`discord.py`) — multi-persona co-presence in one channel via webhook username/avatar override, so this doesn't need per-persona bot tokens.
- **Telegram** (`python-telegram-bot`) — no avatar-override trick, so personas are distinguished by name prefix in the message.
- **Web** (Next.js/React, pnpm) — our own chat UI; the surface with the most freedom and the most that's genuinely ours to design.

More platforms later, added as new adapters against the same core.

## Docker

A `docker-compose.yml` runs backend + web with one command — real payoff given `openhands-tools`' 186-package footprint, without reversing the local-first decision (it still runs on the user's own machine). Also the intended home for real tool-execution sandboxing later, since `openhands-tools` ships none itself. See `project_structure.md` for the scoping and setup details.

## Approvals across surfaces

One provider-neutral `ask_user(questions)` contract sits behind every surface — structured options, multi-select, free-text override, and a tagged intent (e.g. `plan-review`) that changes presentation without changing how the answer is encoded. Discord renders it as native buttons/components, Telegram as inline keyboards, the web UI as custom controls; a plain-text reply ("approve" / "reject") is the universal fallback everywhere. Approval is just one `intent` this contract carries — the same seam covers any point a persona needs to ask a human something, not only approve/reject.

## Build sequence

1. **Discord first** — the webhook trick proves the persona/event model cheaply, with minimal new surface-specific code.
2. **Telegram second** — no avatar override forces the core abstraction to handle a more constrained surface honestly.
3. **Web last** — no external API constraining it, so building it last (once the core abstraction is proven) avoids UI code leaking assumptions back into the core.

## Explicitly out of scope for now

Enterprise data governance, SSO/RBAC, self-hosting packaging for other teams, an agent/persona marketplace, additional platforms beyond the three above. These were part of the original doc's longer-term vision and aren't blocking the first build.
