/**
 * Client for the backend's web_adapter (adapters/web_adapter/api.py).
 * Every screen talks to the backend only through this module.
 *
 * The six interfaces given in the brief (Persona, Conversation, Message,
 * AskQuestion, AskAnswer) are kept EXACTLY as specified — every field
 * below the "--- additive ---" comment in Persona/Message is a new
 * optional field, added because ActivityBlock/DiffChip (and the sibling
 * profile/persona-management screens) can't render without them. Additive
 * only, so the backend and both sibling agents still compile against the
 * originally specified shape.
 */

// The five modes a persona can run under — tapestry_modes_models_personas_spec.md
// §1. Manual is today's original (and still default) behavior: every
// mutating tool call asks first. See that spec for the full per-mode
// breakdown (Accept edits/Auto/Plan/Bypass).
export type Mode = "manual" | "accept_edits" | "auto" | "plan" | "bypass";

export interface Persona {
  id: string;
  name: string;
  role: string;
  model: string;
  status: "online" | "busy" | "paused" | "offline";
  color: string;
  // --- additive (optional) — needed by the profile screen and the
  // persona-management edit form. Not present on the wire yet? Treat as
  // undefined and render nothing. `systemPrompt` is the one field name
  // for this concept end-to-end: backend's core/personas.py Persona
  // model calls it `system_prompt`, and createPersona/updatePersona
  // below already used `systemPrompt` — this used to be a second name
  // (`bio`) for the same field, reconciled to one name.
  systemPrompt?: string;
  tools?: string[];
  mcp?: string[];
  // --- additive (optional), tapestry_modes_models_personas_spec.md §3 —
  // every field here mirrors backend/tapestry/core/personas.py's Persona
  // one-for-one, camelCased. All optional: a persona with none of these
  // set behaves exactly as it always has.
  fallbackModels?: string[];
  guardianModel?: string;
  reasoningEffort?: string;
  defaultMode?: Mode;
  maxTurns?: number;
  maxDelegationDepth?: number;
}

export interface Conversation {
  id: string;
  kind: "dm" | "group";
  name?: string;
  personaIds: string[];
  lastPreview?: string;
  updatedAt: string;
  // The lead persona's (personaIds[0]) current effective mode/model —
  // session/global scope only, per spec §1.6/§2.2 (a "once" override is
  // deliberately not reflected here: it's a one-shot value for the very
  // next turn, not standing conversation state worth surfacing as "the"
  // current mode/model).
  mode: Mode;
  model: string;
}

export interface Message {
  id: string;
  conversationId: string;
  actor: string;
  text: string;
  timestamp: string;
  eventType: string;
  // --- additive (optional) — structured payloads the prototype renders
  // inline under a message. ActivityBlock/DiffChip need these to have
  // anything to render; a plain text message simply omits them.
  activity?: {
    label: string;
    done: boolean;
    result?: string;
  };
  diff?: {
    taskId: string;
    files: number;
    add: number;
    del: number;
  };
  // Added by the approvals pass (components/approvals/ApprovalCard.tsx),
  // additive-only per the comment this replaces: a message carrying an
  // `approval` is the ask_user(...) contract's approval intent rendered
  // inline — same AskQuestion shape answerAsk() already consumes elsewhere,
  // so ApprovalCard/ApprovalActions don't need a second question shape.
  approval?: AskQuestion;
}

export interface AskQuestion {
  id: string;
  question: string;
  detail?: string;
  options?: string[];
  multiSelect?: boolean;
  intent?: string;
  // --- additive: links an approval-intent question back to the task it
  // gates (backend's core/ask.py AskQuestion.related_task_id, present in
  // the model since the graph/build.py pass but never exposed on the wire
  // until now). Lets getApprovalForDiff below find "the approval question
  // for this diff" directly instead of a same-conversation heuristic scan.
  relatedTaskId?: string;
}

export interface AskAnswer {
  id: string;
  selected?: string[];
  custom?: string;
}

export interface ConversationEvent {
  type: string;
  payload: unknown;
}

// --- Diff detail (full per-line content for the diff screen) ---
//
// Message.diff (above) is a SUMMARY only (file count + total add/del) —
// enough for DiffChip's inline chip, not enough for DiffViewer's file
// tabs + line-numbered content. This is the real, separate shape for
// that: one file's real `git diff` hunks, parsed server-side by
// backend/tapestry/graph/diff_capture.py, not reconstructed here.

export interface DiffLine {
  type: "add" | "del" | "ctx";
  lineNumber: number;
  content: string;
}

export interface DiffFile {
  name: string;
  lines: DiffLine[];
}

export interface DiffDetail {
  taskId: string;
  title: string;
  fileCount: number;
  additions: number;
  deletions: number;
  files: DiffFile[];
}

export async function getDiffDetail(
  conversationId: string,
  taskId: string,
): Promise<DiffDetail | null> {
  try {
    return await request<DiffDetail>(
      `/api/conversations/${encodeURIComponent(conversationId)}/diff/${encodeURIComponent(taskId)}`,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

// --- Pending approvals inbox (Activity screen's "Needs your input") ---
//
// Wraps the backend's already-existing GET /api/asks/pending — real since
// the web adapter's first pass, just never called from the frontend
// (components/settings/../app/activity/page.tsx did its own N+1
// conversation scan via lib/safeApi.ts instead).

export interface PendingApproval {
  conversationId: string;
  conversationLabel: string;
  question: AskQuestion;
}

export async function getPendingApprovals(): Promise<PendingApproval[]> {
  return request<PendingApproval[]>("/api/asks/pending");
}

// --- Cross-conversation activity feed (Activity screen's "Running now" /
// "Recent") ---
//
// No per-conversation endpoint can answer this — it's the one screen that
// genuinely needs a cross-conversation view of the event log. "running"
// reflects live in-flight tool calls (ephemeral, in-memory on the
// backend — the event log itself only ever records a tool call's FINAL
// result, never "still going"); "recent" reflects real persisted
// task/delegation history.

export interface ActivityItem {
  conversationId: string;
  conversationLabel: string;
  actor: string;
  label: string;
  timestamp: string;
  taskId?: string;
}

export interface ActivityFeed {
  running: ActivityItem[];
  recent: ActivityItem[];
}

export async function getActivity(): Promise<ActivityFeed> {
  return request<ActivityFeed>("/api/activity");
}

// --- System status (Settings screen's Platforms / Model Providers /
// Tools & MCP panels) ---
//
// All three panels were hardcoded arrays with no backend call at all
// before this. One endpoint covers all three: which chat surfaces have a
// bot token configured, which LiteLLM providers have an API key
// configured, and metamcp's real live tool/server list.

export interface PlatformStatus {
  name: string;
  detail: string;
  connected: boolean;
  alwaysOn: boolean;
}

export interface ProviderStatus {
  name: string;
  connected: boolean;
}

export interface McpServerStatus {
  name: string;
  connected: boolean;
}

export interface SystemStatus {
  platforms: PlatformStatus[];
  providers: ProviderStatus[];
  metamcp: { running: boolean; serverCount: number };
  mcpServers: McpServerStatus[];
}

export async function getStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/api/status");
}

// Server-rendered pages (app/**/page.tsx Server Components) run this
// module inside the Node process — under Docker that's a SEPARATE
// container from the browser, so "localhost" means something different in
// each: to the browser it's the host machine (where the backend's port is
// published), to the server-side Node process it's that container's own
// loopback, which the backend container is not on. INTERNAL_API_URL (not
// NEXT_PUBLIC_-prefixed, so Next.js never inlines it into the client
// bundle) lets Docker Compose point server-side fetches at the backend
// service's Compose-internal DNS name, while the browser keeps using
// NEXT_PUBLIC_API_URL. Outside Docker (native dev, or any single-host
// setup) the two are simply the same value, so this falls back to
// NEXT_PUBLIC_API_URL when INTERNAL_API_URL isn't set.
const API_URL =
  typeof window === "undefined"
    ? (process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "")
    : (process.env.NEXT_PUBLIC_API_URL ?? "");

class ApiError extends Error {
  status: number;
  constructor(path: string, status: number, statusText: string) {
    super(`Tapestry API ${path} failed: ${status} ${statusText}`);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new ApiError(path, res.status, res.statusText);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export async function getPersonas(): Promise<Persona[]> {
  return request<Persona[]>("/api/personas");
}

/**
 * Request payload for create/update — NOT the same as `Persona` above.
 *
 * Reconciled: both this and `Persona.systemPrompt` now use the one name
 * (per the scoped spec's own term, "its own standing instructions (a
 * system prompt)"), matching backend/tapestry/core/personas.py's
 * `Persona.system_prompt` field. This used to be a naming mismatch
 * (`bio` on the response type, `systemPrompt` here) — fixed everywhere
 * it was used: lib/mockData.ts, PersonaEditForm.tsx, PersonaProfileView.tsx.
 */
export interface PersonaDraft {
  name: string;
  role: string;
  model: string;
  status?: Persona["status"];
  color?: string;
  systemPrompt?: string;
  tools?: string[];
  mcp?: string[];
  fallbackModels?: string[];
  guardianModel?: string;
  reasoningEffort?: string;
  defaultMode?: Mode;
  maxTurns?: number;
  maxDelegationDepth?: number;
}

export async function createPersona(draft: PersonaDraft): Promise<Persona> {
  return request<Persona>("/api/personas", {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

export async function updatePersona(id: string, draft: Partial<PersonaDraft>): Promise<Persona> {
  return request<Persona>(`/api/personas/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(draft),
  });
}

export async function getConversations(): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations");
}

export interface ConversationDraft {
  kind: "dm" | "group";
  name?: string;
  personaIds: string[];
}

// Backend mints the id: `dm-{personaId}` for a dm, `grp-{uuid}` for a
// group (backend/tapestry/adapters/web_adapter/api.py's create_conversation)
// — callers read it off the returned Conversation rather than guessing it.
// Idempotent for a dm re-POSTing the same persona.
export async function createConversation(draft: ConversationDraft): Promise<Conversation> {
  return request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

export async function getMessages(conversationId: string): Promise<Message[]> {
  return request<Message[]>(`/api/conversations/${encodeURIComponent(conversationId)}/messages`);
}

export async function sendMessage(conversationId: string, text: string): Promise<Message> {
  return request<Message>(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function answerAsk(conversationId: string, answers: AskAnswer[]): Promise<void> {
  await request<void>(`/api/conversations/${encodeURIComponent(conversationId)}/ask/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

// --- Modes and model switching (tapestry_modes_models_personas_spec.md
// §1.6/§2.2) — both post an event the backend appends to the conversation's
// log (mode/changed, persona/model_switched), read back via Conversation.mode
// / .model above.

export async function setConversationMode(
  conversationId: string,
  personaId: string,
  mode: Mode,
): Promise<void> {
  await request<void>(`/api/conversations/${encodeURIComponent(conversationId)}/mode`, {
    method: "POST",
    body: JSON.stringify({ mode, personaId }),
  });
}

export type ModelSwitchScope = "once" | "session";

export async function setConversationModel(
  conversationId: string,
  personaId: string,
  model: string,
  scope: ModelSwitchScope,
): Promise<void> {
  await request<void>(`/api/conversations/${encodeURIComponent(conversationId)}/model`, {
    method: "POST",
    body: JSON.stringify({ model, personaId, scope }),
  });
}

/**
 * Opens a WebSocket to the given conversation's live event stream and
 * calls `onEvent` for every frame received (parsed as
 * `{type, payload}`). Returns an unsubscribe function that closes the
 * socket — call it on unmount.
 */
export function subscribeToConversation(
  conversationId: string,
  onEvent: (event: ConversationEvent) => void,
): () => void {
  if (typeof WebSocket === "undefined") {
    // SSR / non-browser environment — no-op subscription.
    return () => {};
  }

  const wsUrl = `${API_URL.replace(/^http/, "ws")}/ws/conversations/${encodeURIComponent(conversationId)}`;
  const socket = new WebSocket(wsUrl);

  const handleMessage = (evt: MessageEvent) => {
    try {
      const parsed = JSON.parse(evt.data as string) as ConversationEvent;
      onEvent(parsed);
    } catch {
      // Malformed frame — ignore rather than crash the subscriber.
    }
  };

  socket.addEventListener("message", handleMessage);

  return () => {
    socket.removeEventListener("message", handleMessage);
    socket.close();
  };
}
