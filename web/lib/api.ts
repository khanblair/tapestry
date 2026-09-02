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

export interface Persona {
  id: string;
  name: string;
  role: string;
  model: string;
  status: "online" | "busy" | "paused" | "offline";
  color: string;
  // --- additive (optional) — needed by the profile screen (bio, tools)
  // and the persona-management edit form (tools, mcp). Not present on the
  // wire yet? Treat as undefined and render nothing.
  bio?: string;
  tools?: string[];
  mcp?: string[];
}

export interface Conversation {
  id: string;
  kind: "dm" | "group";
  name?: string;
  personaIds: string[];
  lastPreview?: string;
  updatedAt: string;
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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

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
 * KNOWN NAMING MISMATCH, not yet reconciled: this uses `systemPrompt`
 * (per the scoped spec's own term, "its own standing instructions (a
 * system prompt)"), while the response type `Persona` calls the same
 * concept `bio` (this file's earlier additive field, already relied on
 * by lib/mockData.ts, lib/personaDetails.ts, and the profile screen).
 * Both names refer to the same thing on the wire. Before wiring this up
 * to the real backend, someone needs to pick one name and rename the
 * other everywhere it's used — flagged here rather than silently
 * papered over, since renaming unilaterally right now would touch
 * several sibling-owned files that already have passing tests against
 * the current names.
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
