# Tapestry — Project Structure

**Status: built.** This document was the pre-build plan; the tree below is what it was scoped against, not a live listing. All four backend phases and the full frontend now exist and are tested — 347 backend tests, 28 frontend unit tests, 16/18 e2e (2 correctly skipped as device-inapplicable), both Docker images build and run for real. Real implementation always drifts a little from a plan (extra `conftest.py` fixtures, a few extra `lib/` helper modules the frontend agents added as they reconciled with each other, `config.py`/`main.py` filled in) — the code and its own tests are the source of truth for exact current file contents; this doc stays useful for the *reasoning* behind the shape, which didn't change. The one drift worth correcting explicitly, since it was flagged as a real gap by name at several points during the build: the test tree below undercounted real test coverage. Actual test files, beyond what's listed: `tests/core/{conftest,test_conversations,test_approvals}.py`, `tests/models/test_litellm_client_stream.py`, `tests/storage/test_db.py`, `tests/skills/{test_loader,test_catalog_sync}.py`, `tests/graph/{conftest,test_checkpointer,test_streaming}.py`, `tests/tools/test_mcp_client.py`, `tests/adapters/conftest.py`, and a top-level `tests/test_main.py` — plus `web/tests/unit/components/{ActivityBlock,DiffViewer,PersonaEditForm}.test.tsx`.

Original scaffold description, historical: full plan, reflecting the scoped spec plus everything verified against real source in `vendor-research/`.

```
tapestry/
├── .gitignore
├── .env.example                               # DISCORD_BOT_TOKEN, TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY,
│                                               # DEEPSEEK_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY,
│                                               # METAMCP_API_KEY — read by docker-compose.yml
├── README.md
│
├── docs/
│   ├── tapestry_scoped_spec.md                # current scoped spec — what we're actually shipping
│   └── vendor-research/
│       ├── ANALYSIS-langgraph.md
│       ├── ANALYSIS-litellm.md
│       ├── ANALYSIS-openhands-tools.md
│       ├── ANALYSIS-metamcp.md
│       ├── ANALYSIS-discordpy.md
│       ├── ANALYSIS-python-telegram-bot.md
│       └── ANALYSIS-deepseek-harness.md       # loop-engineering + skills source, not a runtime dependency
│
├── personas/                                  # actual persona configs — data, not code. Same split as skills/
│   │                                           # below: content lives here, the service that reads it lives
│   │                                           # in backend/tapestry/core/personas.py
│   ├── ada.yaml                                # Architect — Claude Opus, plan-before-code, no shell-write
│   ├── rex.yaml                                # Developer — DeepSeek V3.2, file-edit + shell + git
│   ├── vex.yaml                                # Security & QA — Claude Sonnet, read-only shell, test runner
│   └── nova.yaml                               # DevOps — Gemini 3 Pro, deploy pipeline, paused by default
│
├── skills/                                     # bundled default skills — actual SKILL.md content, not code.
│   │                                           # Rank 600 ("bundled") in the discovery order; a project or
│   │                                           # user can add higher-ranked skills without touching this dir
│   ├── systematic-debugging/
│   │   └── SKILL.md
│   ├── test-driven-development/
│   │   └── SKILL.md
│   └── verification-before-completion/
│       └── SKILL.md                            # the self-verification step graph/verify.py enforces in code —
│                                               # this is its human/model-readable procedure form
│
├── backend/                                    # Python service — LangGraph/LiteLLM/openhands-tools all live here
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── Dockerfile
│   ├── .env.example
│   │
│   ├── tapestry/
│   │   ├── __init__.py
│   │   ├── main.py                             # process entrypoint — starts the Discord and Telegram adapters
│   │   │                                       # as sibling asyncio tasks and mounts the web adapter's API,
│   │   │                                       # all on one event loop (per the discord.py-verified pattern)
│   │   ├── config.py                           # loads .env — API keys, bot tokens, metamcp URL, sqlite path
│   │   │
│   │   ├── core/                               # platform-agnostic. No Discord/Telegram/web imports allowed
│   │   │   │                                   # here — this boundary is what lets a 4th surface be "just
│   │   │   │                                   # another adapter"
│   │   │   ├── __init__.py
│   │   │   ├── events.py                       # append-only event log — the actual source of truth
│   │   │   ├── personas.py                     # loads personas/*.yaml; identity: name, model, permissions,
│   │   │   │                                   # standing instructions
│   │   │   ├── conversations.py                # Conversation/Message projections built FROM the event log
│   │   │   ├── delegation.py                   # typed persona-to-persona messages, retry, round caps
│   │   │   ├── ask.py                          # provider-neutral ask_user(questions) -> answers contract;
│   │   │   │                                   # each surface renders it, none of them own its shape
│   │   │   └── approvals.py                    # approve/reject/stop — one `intent` on top of ask.py, not
│   │   │                                       # its own protocol
│   │   │
│   │   ├── graph/                              # LangGraph orchestration
│   │   │   ├── __init__.py
│   │   │   ├── build.py                        # persona node → approval node → execute node, as THREE
│   │   │   │                                   # separate nodes — interrupt() re-executes its whole node on
│   │   │   │                                   # resume, so the approval gate and the side-effecting action
│   │   │   │                                   # can never share one. Interception points named explicitly
│   │   │   │                                   # here rather than left implicit in edges: what enters a turn,
│   │   │   │                                   # the request config, the tool-dispatch gate, turn-close
│   │   │   ├── budgets.py                      # three separate mechanisms, not one counter: a durable
│   │   │   │                                   # per-task round cap, a persisted recursion-depth cap for
│   │   │   │                                   # delegation (so a resumed subagent can't silently reset it),
│   │   │   │                                   # and a cost/token measurement that informs policy rather
│   │   │   │                                   # than gating by itself
│   │   │   ├── verify.py                       # self-verification before "done" — persona-configurable
│   │   │   │                                   # pre-completion check (re-read the diff, re-run tests,
│   │   │   │                                   # restate the ask). Nobody researched solves this; ours to build
│   │   │   ├── checkpointer.py                 # AsyncSqliteSaver — local-first, no Postgres needed for v1
│   │   │   └── streaming.py                    # stream_mode="custom" + StreamWriter (NOT "messages" — that
│   │   │                                       # mode only fires for LangChain's own chat-model wrappers,
│   │   │                                       # not our direct litellm.acompletion() calls)
│   │   │
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   └── litellm_client.py               # call_model(persona, messages, tools); captures
│   │   │                                       # response.usage.cost. A thin policy layer over the raw call:
│   │   │                                       # empty completion = retryable failure, not silent success;
│   │   │                                       # context-window-exceeded normalized to one canonical code
│   │   │
│   │   ├── skills/                             # the CODE that reads skills/ (root) content — packaged,
│   │   │   │                                   # reusable procedures a persona invokes by name; knowledge,
│   │   │   │                                   # not a callable capability like tools/
│   │   │   ├── __init__.py
│   │   │   ├── loader.py                       # parses SKILL.md frontmatter (name, description, whenToUse,
│   │   │   │                                   # disable-model-invocation, user-invocable) + body
│   │   │   ├── registry.py                     # rank-ordered discovery: project skills override user
│   │   │   │                                   # overrides bundled defaults; exposes a name+description-only
│   │   │   │                                   # catalog, full body loads on demand through one tool call
│   │   │   └── catalog_sync.py                 # content-hashes the catalog, appends a full-replacement
│   │   │                                       # event to the event log only when it changes — never edits
│   │   │
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   ├── file_editor.py                  # shim over openhands-tools' FileEditorExecutor + OUR OWN
│   │   │   │                                   # path allow-list — the library ships no sandboxing at all
│   │   │   ├── terminal.py                     # shim over TerminalExecutor(terminal_type="subprocess") to
│   │   │   │                                   # skip tmux
│   │   │   └── mcp_client.py                   # official `mcp` SDK client → metamcp at localhost:12008
│   │   │
│   │   ├── adapters/                           # one per chat surface — translate core events <-> platform
│   │   │   ├── __init__.py
│   │   │   ├── discord_adapter/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bot.py                      # await bot.start(token) as a sibling task, not bot.run()
│   │   │   │   └── webhook_identity.py         # ONE webhook per channel; username/avatar_url swapped per
│   │   │   │                                   # persona
│   │   │   ├── telegram_adapter/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bot.py
│   │   │   │   └── identity.py                 # name-prefixed text — no avatar-override equivalent here
│   │   │   └── web_adapter/
│   │   │       ├── __init__.py
│   │   │       └── api.py                      # the surface the Next.js app talks to (REST + WS/SSE)
│   │   │
│   │   └── storage/
│   │       ├── __init__.py
│   │       ├── db.py                           # SQLite connection + schema
│   │       └── schema.sql
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── core/
│       │   ├── test_events.py
│       │   ├── test_personas.py
│       │   ├── test_delegation.py
│       │   └── test_ask.py
│       ├── graph/
│       │   ├── test_build.py
│       │   ├── test_budgets.py
│       │   └── test_verify.py
│       ├── skills/
│       │   └── test_registry.py
│       ├── tools/
│       │   ├── test_file_editor.py
│       │   └── test_terminal.py
│       └── adapters/
│           ├── test_discord_adapter.py
│           ├── test_telegram_adapter.py
│           └── test_web_adapter.py             # was missing before this pass — the web_adapter had no
│                                               # backend-side test at all
│
├── web/                                        # Next.js frontend — all ten screens from the prototype,
│   │                                           # not the four the first draft of this doc actually listed
│   ├── package.json
│   ├── pnpm-lock.yaml                          # pnpm is the package manager — not npm/yarn
│   ├── next.config.ts
│   ├── tsconfig.json
│   ├── Dockerfile
│   ├── .env.example
│   ├── vitest.config.ts                        # unit/component tests
│   ├── playwright.config.ts                    # e2e — see tests/ below
│   ├── public/
│   │   └── favicon.ico
│   │
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── globals.css
│   │   ├── (roster)/
│   │   │   └── page.tsx                        # conversation list — the app's home. Screen 1
│   │   ├── conversation/[id]/
│   │   │   ├── page.tsx                        # main chat pane. Screen 2
│   │   │   ├── thread/[threadId]/
│   │   │   │   └── page.tsx                    # spun-off thread. Screen 3
│   │   │   └── diff/[taskId]/
│   │   │       └── page.tsx                    # expanded diff/code review. Screen 10
│   │   ├── new-conversation/
│   │   │   └── page.tsx                        # new DM / new group tabs. Screen 4
│   │   ├── search/
│   │   │   └── page.tsx                        # Screen 6
│   │   ├── activity/
│   │   │   └── page.tsx                        # approvals inbox + running tasks + pause-all. Screen 9
│   │   ├── profile/[personaId]/
│   │   │   └── page.tsx                        # read-only persona profile, opened from a name in a
│   │   │                                       # message — distinct from personas/ below. Screen 5
│   │   ├── personas/
│   │   │   ├── page.tsx                        # persona MANAGEMENT list (admin surface, via Settings).
│   │   │   │                                   # Screen 8, not the same screen as profile/ above
│   │   │   └── [personaId]/
│   │   │       └── page.tsx                    # create/edit form
│   │   └── settings/
│   │       └── page.tsx                        # hosts the four SettingsTabs panels below. Screen 7
│   │
│   ├── components/
│   │   ├── roster/
│   │   │   ├── RosterList.tsx
│   │   │   └── RosterRow.tsx
│   │   ├── conversation/
│   │   │   ├── ConversationView.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── Composer.tsx
│   │   │   ├── ActivityBlock.tsx               # collapsible "running pytest..." block
│   │   │   └── DiffChip.tsx                    # inline "3 files changed +142 -8" chip, links to full diff
│   │   ├── diff/
│   │   │   └── DiffViewer.tsx                  # file tabs + line-numbered add/del rendering
│   │   ├── approvals/
│   │   │   ├── ApprovalCard.tsx
│   │   │   └── ApprovalActions.tsx             # Approve/Reject — shared between the inline card, the full
│   │   │                                       # diff screen, and the activity inbox (one shared state)
│   │   ├── persona/
│   │   │   ├── PersonaAvatar.tsx
│   │   │   ├── StatusDot.tsx                   # online/busy/paused/offline
│   │   │   └── PersonaCard.tsx
│   │   ├── settings/
│   │   │   ├── SettingsTabs.tsx
│   │   │   ├── PlatformsPanel.tsx
│   │   │   ├── ModelProvidersPanel.tsx
│   │   │   ├── ToolsAndMcpPanel.tsx
│   │   │   └── AppearancePanel.tsx
│   │   └── ui/                                 # theme-aware primitives shared across every screen
│   │       ├── Button.tsx
│   │       ├── Toggle.tsx
│   │       ├── Modal.tsx                       # desktop centered-modal / mobile full-cover — the one
│   │       │                                   # responsive split every overlay screen shares (see the
│   │       │                                   # routing note below)
│   │       └── ThemeProvider.tsx
│   │
│   ├── lib/
│   │   ├── api.ts                              # client for the backend's web_adapter (REST + WS/SSE)
│   │   └── theme.ts                            # the dark/light tokens from the palette work
│   │
│   └── tests/
│       ├── unit/                               # Vitest + React Testing Library
│       │   ├── components/
│       │   │   ├── MessageBubble.test.tsx
│       │   │   ├── ApprovalCard.test.tsx
│       │   │   ├── DiffChip.test.tsx
│       │   │   └── StatusDot.test.tsx
│       │   └── lib/
│       │       └── api.test.ts
│       └── e2e/                                # Playwright, one project config per breakpoint (mobile/
│           │                                   # tablet/desktop) — the exact click-through flows verified
│           │                                   # by hand against the prototype, now automated
│           ├── responsive-collapse.spec.ts     # roster<->conversation single-pane collapse on mobile
│           ├── approval-flow.spec.ts           # approve/reject state shared across card, diff, activity
│           ├── persona-management.spec.ts      # edit form close doesn't leak stale state — the real bug
│           │                                   # the prototype hit (editingPersona never cleared)
│           └── new-conversation.spec.ts
│
└── docker/
    ├── docker-compose.yml                      # backend + web, one command for a full local run
    └── tool-runner/
        └── Dockerfile                          # NOT v1 — the real fix for "no path sandboxing," stubbed
                                                 # here so the seam exists from day one (see Docker section)
```

Footnote on `docs/vendor-research/`: the seven reports currently sit at the repo's top-level `vendor-research/` (the seven cloned repos used to verify them have been deleted — 413MB freed, only the `.md` reports remain, 148KB total). Moving them under `docs/` per the tree above is still a pending step, not yet done.

**Routing note on the modal screens** (new-conversation, search, activity, profile, personas, and the diff/thread views): the tree above shows each as a plain route because that's what actually needs to exist, but the desktop-centered-modal / mobile-full-page split the prototype validated is exactly what Next.js's App Router **parallel + intercepting routes** feature (`@modal` slot + `(.)folder` convention) is built for — a route like `/search` still resolves as a real, shareable, full page on mobile or on direct navigation, while intercepted from `/conversation/[id]` it renders as a modal over the conversation underneath on desktop. Worth building this way from the start rather than faking it with client-side overlay state, since it's the same responsive behavior the prototype already proved out, just backed by real URLs instead of JS state.

## Frontend testing

There were no frontend tests at all before this pass — added two layers, matched to what actually broke when the prototype was manually tested:

- **Vitest + React Testing Library** for the shared primitives (`components/ui/`, `components/approvals/`, `components/persona/`) — the kind of thing that's cheap to unit-test and easy to regress silently (a `StatusDot` rendering the wrong color for a status, a `DiffChip` not showing the right +/- counts).
- **Playwright**, one project per breakpoint (mobile/tablet/desktop), covering exactly the click-through flows verified by hand earlier: roster/conversation single-pane collapse on mobile, approval state staying in sync across the inline card/diff screen/activity inbox, and — deliberately named after the actual bug — persona-edit close not leaking `editingPersona` into the next screen. Manual QA caught these three real bugs once; e2e tests are what stop them from coming back silently.

## Pinned dependencies (backend/pyproject.toml), as verified

| Package | Version | Note |
|---|---|---|
| `langgraph` | `1.2.11` | MIT |
| `langgraph-checkpoint` | `4.2.0` | MIT |
| `langgraph-checkpoint-sqlite` | `3.1.1` | MIT — `AsyncSqliteSaver` |
| `litellm` | `1.99.0` | MIT — **do not** install the `proxy` extra (that's the only path an enterprise-licensed package enters). The cloned repo's own source reported `1.101.0`; that version was never actually published to PyPI — verified live against pypi.org during implementation |
| `openhands-sdk` | `1.44.1` | MIT — **must match** `openhands-tools`' version exactly, or import breaks |
| `openhands-tools` | `1.44.1` | MIT |
| `mcp` | official SDK | client to metamcp at `localhost:12008` |
| `discord.py` | latest stable | MIT (repo HEAD is a pre-2.8 alpha; pin to the latest tagged release, not HEAD) |
| `python-telegram-bot` | `22.8.0` | **LGPL-3.0**, not MIT — fine as a normal dependency, don't vendor-and-modify |

Explicitly **not** a dependency: `langgraph-cli` (pulls in the Elastic-licensed `langgraph-api`) and `openhands-aci` (unrelated package from a different, older OpenHands repo).

## Notes

- `core/` has no knowledge of Discord, Telegram, or the web app — every adapter only talks to `core/` through `events.py`, `conversations.py`, and `ask.py`/`approvals.py`. That boundary is the whole point of the adapter pattern from the scoped spec.
- Tool execution (`tools/`) is deliberately separate from the graph/model code — `openhands-tools` alone pulls ~186 transitive packages (litellm, Playwright, fastmcp). That boundary is also exactly where the Docker tool-runner (below) slots in later without touching the graph.
- Build sequence stays Discord → Telegram → web, per the scoped spec — so `discord_adapter/` is the first thing that gets real code.
- Any long-running, resumable, bracketed operation logged to `events.py` (a turn, a compaction pass, anything else with a start/end pair) follows one house rule: on crash recovery, close the orphan with a repair event carrying a reason code that's reserved *exclusively* for post-hoc repair and never emitted by live code. Detect the crash, don't paper over it with a false "finished" event.

## Loop engineering & skills

Verified against DeepSeek Harness (MIT, real source — `vendor-research/ANALYSIS-deepseek-harness.md`), which is worth reading carefully for exactly two things and not for a third it oversells.

**Worth taking:** the turn/step interception-point vocabulary (what enters a turn, request config, tool-dispatch gate, turn-close decision) as a naming discipline for the LangGraph node split we already need; layered budgets kept as three genuinely separate mechanisms rather than one counter; the crash-recovery-by-synthetic-close-out technique (above); and the `SKILL.md` format nearly verbatim, since it's close enough to Claude Code's own shape that skill files should need zero translation between the two.

**Not worth taking:** the "everything is a plugin, including the loop" framing. In the actual source, that means exactly one shipped ReAct loop plus a from-scratch reimplementation escape hatch — not a menu of loop strategies. LangGraph already solves per-persona-configurable control flow properly; there's nothing here that argues for reinventing it. Likewise, the 8,846-file plugin/DI machinery (Cordis, vendored) exists to support third-party plugin distribution — a need this project doesn't have at this scale. A plain Python registry plus a few ordered hook lists gets the interception-point value without that cost.

**Explicitly not solved by anything researched so far:** self-verification before a persona declares a task done. Every harness looked at (this one, openhands-tools, Hermes Bot Mode) is missing a real pre-completion check. That's `graph/verify.py` above — genuine design work, not integration.

**Skills vs. tools, concretely:** a tool is always in the model's visible schema (paid in context every step) and does something. A skill costs one line in a catalog until named and loaded on demand — closer to a colleague's checklist than a callable function. `skills/` (root) holds the actual content; `backend/tapestry/skills/` holds the loader/registry/catalog-sync code that reads it.

## Docker

Worth doing, scoped narrowly — not a wholesale move to hosted/cloud, which the local-first decision already ruled out on purpose.

**`docker-compose.yml` (backend + web, day one):** one command to run the full stack with pinned Python 3.12 and Node/pnpm versions, instead of everyone hand-installing them. Real payoff given `openhands-tools`' 186-package footprint — that dependency tree lives in the image, not on anyone's machine. Two setup details this isn't hand-wavy about: the backend container needs the target repo bind-mounted in (the agent's whole job is editing real files in a real worktree), and it needs git identity/credentials forwarded in (an SSH-agent socket mount, or a mounted `.gitconfig`) so commits and pushes still work as the actual user. metamcp is **not** part of this compose file — it's already running standalone at `localhost:12008`; adding a second instance would just create a competing one.

**`docker/tool-runner/` (not v1, but the real fix, so the seam exists now):** the vendor research already flagged that `FileEditorExecutor`/`TerminalExecutor` ship with *no path sandboxing* — as-is, they'll happily touch anything the OS process can write to. A minimal container that tool calls get shelled into, with only the target worktree bind-mounted, is what actually closes that gap (matching the original spec's "shell execution happens inside sandboxed environments") — a real sandbox boundary instead of an allow-list we'd otherwise have to hand-roll and trust. Fine to ship v1 with the allow-list as an interim measure; this is the fast-follow, not a blocker.

**One honest caveat, since this is a Mac:** Docker's bind-mount I/O on macOS (virtiofs/gRPC-FUSE) has real overhead for heavy file-watching workloads. Not a blocker for a coding agent's edit/run/test loop, but worth knowing if things feel sluggish — it's the mount, not the code.

**Day-to-day backend development stays native** (a local `uv`/venv), not inside a container — rebuilding an image on every code change is more friction than it's worth during active iteration. Docker earns its keep for "run the whole stack reliably" and "sandbox what the agent touches," not for the inner dev loop.
