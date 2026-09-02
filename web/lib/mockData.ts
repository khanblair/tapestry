// Local supplement to the shared lib/api.ts contract, same pattern as
// lib/personaDetails.ts (see that file's header comment): the backend isn't
// reachable in this environment, and lib/api.ts's request() throws on any
// non-2xx or network failure rather than falling back to fixture data. The
// screens built in this pass (search, activity, diff, thread, new
// conversation) need something to render against regardless, so this module
// holds the fixture data — ported verbatim from the HTML prototype's
// PERSONAS/CONVOS/GROUP_MESSAGES/THREAD_MESSAGES/DIFF_FILES — typed against
// the REAL Persona/Conversation/Message shapes from lib/api.ts (kind,
// personaIds, actor, timestamp, diff{taskId,files,add,del}), not an earlier
// draft of that contract.
//
// Flagged for reconciliation: once the backend exists, lib/safeApi.ts's
// fallbacks (and this file) should go away in favor of always hitting the
// real endpoints.

import type {
  ActivityFeed,
  Conversation,
  Message,
  Persona,
  SystemStatus,
} from "./api";

export const MOCK_PERSONAS: Persona[] = [
  {
    id: "ada",
    name: "Ada",
    role: "Architect",
    color: "#3B82F6",
    model: "Claude Opus 4.8",
    status: "online",
    systemPrompt: "Plans system design before code gets written. Reviews architectural decisions and flags risk early, before a developer agent starts implementing.",
    tools: ["File read", "Read-only shell", "MCP: filesystem"],
    mcp: ["filesystem", "git (read)"],
  },
  {
    id: "rex",
    name: "Rex",
    role: "Developer",
    color: "#8B5CF6",
    model: "DeepSeek V3.2",
    status: "busy",
    systemPrompt: "Implements features inside isolated worktrees. Writes tests alongside code, not after, and opens a diff for review before anything merges.",
    tools: ["File edit", "Shell exec", "Git", "MCP: filesystem, git"],
    mcp: ["filesystem", "git", "terminal"],
  },
  {
    id: "vex",
    name: "Vex",
    role: "Security & QA",
    color: "#F43F5E",
    model: "Claude Sonnet 5",
    status: "online",
    systemPrompt: "Reviews diffs for vulnerabilities before merge and runs the test suite to block regressions. Has no shell-write access by design.",
    tools: ["File read", "Read-only shell", "Test runner"],
    mcp: ["filesystem (read)"],
  },
  {
    id: "nova",
    name: "Nova",
    role: "DevOps",
    color: "#14B8A6",
    model: "Gemini 3 Pro",
    status: "paused",
    systemPrompt: "Owns CI, deploys, and infrastructure changes. Paused until the OAuth work is ready to ship — no need to burn budget standing by.",
    tools: ["Shell exec", "Deploy pipeline", "MCP: git, cloud"],
    mcp: ["git", "cloud-deploy"],
  },
];

export const MOCK_CONVERSATIONS: Conversation[] = [
  { id: "dm-ada", kind: "dm", personaIds: ["ada"], lastPreview: "Sent the auth architecture proposal", updatedAt: minutesAgo(2) },
  { id: "dm-rex", kind: "dm", personaIds: ["rex"], lastPreview: "Fixed — re-running tests now", updatedAt: minutesAgo(4) },
  { id: "dm-vex", kind: "dm", personaIds: ["vex"], lastPreview: "Token scoping looks right now", updatedAt: minutesAgo(6) },
  { id: "dm-nova", kind: "dm", personaIds: ["nova"], lastPreview: "Standing by for the deploy", updatedAt: minutesAgo(60) },
  {
    id: "grp-auth",
    kind: "group",
    name: "#auth-rework",
    personaIds: ["ada", "rex", "vex"],
    lastPreview: "Rex: opened diff — 3 files changed",
    updatedAt: minutesAgo(4),
  },
];

function minutesAgo(min: number): string {
  return new Date(Date.now() - min * 60_000).toISOString();
}

export const APPROVAL_QUESTION_ID = "appr-1";

export const MOCK_MESSAGES: Record<string, Message[]> = {
  "grp-auth": [
    { id: "m1", conversationId: "grp-auth", actor: "you", text: "Can we add OAuth login with Google and GitHub before Friday?", timestamp: "10:02", eventType: "message" },
    {
      id: "m2",
      conversationId: "grp-auth",
      actor: "ada",
      text: "Proposal: OAuth via next-auth, Google first, GitHub second. Token exchange stays server-side, nothing touches the client. I'll hand this to @Rex to build.",
      timestamp: "10:03",
      eventType: "message",
    },
    { id: "m3", conversationId: "grp-auth", actor: "ada", text: "@Rex go ahead on the Google provider first — worktree feat/oauth-google.", timestamp: "10:04", eventType: "message" },
    {
      id: "m4",
      conversationId: "grp-auth",
      actor: "rex",
      text: "On it.",
      timestamp: "10:06",
      eventType: "message",
      activity: { label: "Running pytest tests/auth/", done: true, result: "12 passed" },
    },
    {
      id: "m5",
      conversationId: "grp-auth",
      actor: "rex",
      text: "First pass is in.",
      timestamp: "10:19",
      eventType: "message",
      diff: { taskId: "oauth-google", files: 3, add: 142, del: 8 },
    },
    {
      id: "m6",
      conversationId: "grp-auth",
      actor: "vex",
      text: "@Rex found one issue — the access token isn't scoped to email only, it's requesting full profile write. Line 42 of oauth/google.py.",
      timestamp: "10:24",
      eventType: "message",
    },
    {
      id: "m7",
      conversationId: "grp-auth",
      actor: "rex",
      text: "Good catch, fixing.",
      timestamp: "10:26",
      eventType: "message",
      activity: { label: "Running pytest tests/auth/", done: true, result: "14 passed" },
    },
    {
      id: "m8",
      conversationId: "grp-auth",
      actor: "rex",
      text: "Scope narrowed to email profile. Ready to merge.",
      timestamp: "10:31",
      eventType: "message",
      approval: {
        id: APPROVAL_QUESTION_ID,
        question: "Merge feat/oauth-google → main",
        detail: "Rex wants to merge 3 changed files into main. Tests pass, Vex has reviewed.",
        intent: "approval",
        relatedTaskId: "oauth-google",
      },
    },
  ],
};

export const MOCK_THREAD_MESSAGES: Record<string, Message[]> = {
  t1: [
    {
      id: "th1",
      conversationId: "grp-auth",
      actor: "vex",
      text: "Flagging this before it goes further — requesting full profile write when we only need the email is the kind of thing that's easy to miss in review.",
      timestamp: "10:24",
      eventType: "message",
    },
    { id: "th2", conversationId: "grp-auth", actor: "you", text: "Good catch. Rex, can you narrow that scope?", timestamp: "10:25", eventType: "message" },
    { id: "th3", conversationId: "grp-auth", actor: "rex", text: "Yep, one-line change in the provider config.", timestamp: "10:25", eventType: "message" },
    { id: "th4", conversationId: "grp-auth", actor: "vex", text: "Confirmed fixed on the re-run. Approving from my side.", timestamp: "10:33", eventType: "message" },
  ],
};

// Re-exported (not redeclared) from lib/api.ts — that's the real,
// authoritative shape now that GET /api/conversations/{id}/diff/{taskId} is
// a real endpoint. A separate, looser local type here (lineNumber used to be
// `string | number`) would let the mock fixture widen the real contract;
// `export type { ... } from` keeps every existing import of these three
// names (this file, safeApi.ts, DiffScreenView.tsx, DiffViewer.tsx,
// DiffViewer.test.tsx) working unchanged.
export type { DiffLine, DiffFile, DiffDetail } from "./api";
import type { DiffDetail } from "./api";

export const MOCK_DIFFS: Record<string, DiffDetail> = {
  "oauth-google": {
    taskId: "oauth-google",
    title: "feat/oauth-google",
    fileCount: 3,
    additions: 142,
    deletions: 8,
    files: [
      {
        name: "oauth/google.py",
        lines: [
          { type: "ctx", lineNumber: 40, content: "    def build_auth_url(self):" },
          { type: "del", lineNumber: 41, content: '        scope = "openid profile email profile.write"' },
          { type: "add", lineNumber: 41, content: '        scope = "openid email profile"' },
          { type: "ctx", lineNumber: 42, content: "        return self._authorize_url(scope=scope)" },
          { type: "ctx", lineNumber: 43, content: "" },
          { type: "add", lineNumber: 44, content: "    def refresh_if_expiring(self, token):" },
          { type: "add", lineNumber: 45, content: "        if token.expires_in < 60:" },
          { type: "add", lineNumber: 46, content: "            return self._refresh(token)" },
        ],
      },
      {
        name: "oauth/routes.py",
        lines: [
          { type: "ctx", lineNumber: 12, content: 'router = APIRouter(prefix="/auth")' },
          { type: "add", lineNumber: 13, content: "" },
          { type: "add", lineNumber: 14, content: '@router.get("/google/callback")' },
          { type: "add", lineNumber: 15, content: "def google_callback(code: str):" },
          { type: "add", lineNumber: 16, content: "    return exchange_code(code)" },
        ],
      },
      {
        name: "tests/auth/test_google.py",
        lines: [
          { type: "add", lineNumber: 1, content: "def test_scope_excludes_profile_write():" },
          { type: "add", lineNumber: 2, content: '    assert "profile.write" not in build_auth_url()' },
        ],
      },
    ],
  },
};

// Fallback for lib/safeApi.ts's safeGetActivity() when GET /api/activity
// isn't reachable — same "Running now" / "Recent" copy the Activity screen
// used to render as static JSX before that endpoint existed.
export const MOCK_ACTIVITY: ActivityFeed = {
  running: [
    {
      conversationId: "grp-auth",
      conversationLabel: "#auth-rework",
      actor: "Rex",
      label: "running pytest tests/auth/",
      timestamp: minutesAgo(0),
    },
  ],
  recent: [
    {
      conversationId: "grp-auth",
      conversationLabel: "#auth-rework",
      actor: "Ada",
      label: "proposed OAuth architecture",
      timestamp: minutesAgo(30),
    },
    {
      conversationId: "grp-auth",
      conversationLabel: "#auth-rework",
      actor: "Vex",
      label: "flagged token scope issue",
      timestamp: minutesAgo(8),
    },
  ],
};

// Fallback for lib/safeApi.ts's safeGetStatus() when GET /api/status isn't
// reachable — the same rows the three Settings panels used to hardcode
// directly before that endpoint existed.
export const MOCK_STATUS: SystemStatus = {
  platforms: [
    { name: "Discord", detail: "Connected as @tapestry-bot", connected: true, alwaysOn: false },
    { name: "Telegram", detail: "Not connected", connected: false, alwaysOn: false },
    { name: "Web", detail: "Always on", connected: true, alwaysOn: true },
  ],
  providers: [
    { name: "Anthropic", connected: true },
    { name: "DeepSeek", connected: true },
    { name: "Gemini", connected: true },
    { name: "Qwen", connected: false },
    { name: "OpenRouter", connected: true },
  ],
  metamcp: { running: true, serverCount: 4 },
  mcpServers: [
    { name: "filesystem", connected: true },
    { name: "git", connected: true },
    { name: "terminal", connected: true },
    { name: "browser", connected: true },
  ],
};
