// Resilient wrappers around lib/api.ts's request()-based fetchers, which
// throw on any non-2xx status or network failure with no built-in fallback.
// There's no backend running in this environment, so every screen built in
// this pass calls through here rather than the raw lib/api.ts functions
// directly — falls back to lib/mockData.ts's fixtures so the UI stays
// demoable and testable before a real backend exists. Once one does, these
// wrappers (and lib/mockData.ts) should be deleted and callers should go back
// to calling lib/api.ts directly.

import {
  getConversations as apiGetConversations,
  getPersonas as apiGetPersonas,
  getMessages as apiGetMessages,
  getDiffDetail as apiGetDiffDetail,
  getPendingApprovals as apiGetPendingApprovals,
  getActivity as apiGetActivity,
  getStatus as apiGetStatus,
  type Conversation,
  type Message,
  type Persona,
  type ActivityFeed,
  type SystemStatus,
  type PendingApproval,
} from "./api";
import {
  MOCK_CONVERSATIONS,
  MOCK_MESSAGES,
  MOCK_PERSONAS,
  MOCK_THREAD_MESSAGES,
  type DiffDetail,
  MOCK_DIFFS,
  MOCK_ACTIVITY,
  MOCK_STATUS,
} from "./mockData";

// Re-exported (not redeclared) from lib/api.ts, same reasoning as
// DiffDetail above: GET /api/asks/pending is real now, so its response
// shape is the authoritative one. Kept as a named export here so existing
// `import { type PendingApproval } from "@/lib/safeApi"` call sites don't
// need to change.
export type { PendingApproval };

export async function safeGetConversations(): Promise<Conversation[]> {
  try {
    return await apiGetConversations();
  } catch {
    return MOCK_CONVERSATIONS;
  }
}

export async function safeGetPersonas(): Promise<Persona[]> {
  try {
    return await apiGetPersonas();
  } catch {
    return MOCK_PERSONAS;
  }
}

export async function safeGetMessages(conversationId: string): Promise<Message[]> {
  try {
    return await apiGetMessages(conversationId);
  } catch {
    return MOCK_MESSAGES[conversationId] ?? [];
  }
}

export async function getConversationById(id: string): Promise<Conversation | null> {
  const all = await safeGetConversations();
  return all.find((c) => c.id === id) ?? null;
}

export async function getPersonaById(id: string): Promise<Persona | null> {
  const all = await safeGetPersonas();
  return all.find((p) => p.id === id) ?? null;
}

export async function getPersonaMap(): Promise<Map<string, Persona>> {
  const all = await safeGetPersonas();
  return new Map(all.map((p) => [p.id, p]));
}

// No backend endpoint exists yet for "just this thread's messages" — see the
// contract gap noted in the final report. Falls back to lib/mockData.ts's
// MOCK_THREAD_MESSAGES, keyed by threadId.
export async function getThreadMessages(_conversationId: string, threadId: string): Promise<Message[]> {
  return MOCK_THREAD_MESSAGES[threadId] ?? [];
}

// Fetches a diff's full line-level content by (conversationId, taskId) via
// the real GET /api/conversations/{id}/diff/{taskId} endpoint. A thrown
// error (network failure, backend unreachable) falls back to
// lib/mockData.ts's MOCK_DIFFS — but a clean, confirmed 404 (apiGetDiffDetail
// resolves to `null` rather than throwing) is left as `null` rather than
// silently swapped for unrelated mock data.
export async function getDiffDetail(conversationId: string, taskId: string): Promise<DiffDetail | null> {
  try {
    return await apiGetDiffDetail(conversationId, taskId);
  } catch {
    return MOCK_DIFFS[taskId] ?? null;
  }
}

// The Activity screen's "Needs your input" section needs every pending
// approval-intent ask across all conversations. Backed by the real
// GET /api/asks/pending. Falls back to the previous N+1 scan (fetch every
// conversation, then every conversation's messages, filtering for an
// `approval` field) only when that call fails.
export async function getPendingApprovals(): Promise<PendingApproval[]> {
  try {
    return await apiGetPendingApprovals();
  } catch {
    const conversations = await safeGetConversations();
    const out: PendingApproval[] = [];
    for (const convo of conversations) {
      const messages = await safeGetMessages(convo.id);
      for (const m of messages) {
        if (m.approval) {
          out.push({
            conversationId: convo.id,
            conversationLabel: convo.name ?? convo.personaIds[0] ?? convo.id,
            question: m.approval,
          });
        }
      }
    }
    return out;
  }
}

// The Activity screen's "Running now" / "Recent" sections, backed by the
// real GET /api/activity. Falls back to lib/mockData.ts's MOCK_ACTIVITY.
export async function safeGetActivity(): Promise<ActivityFeed> {
  try {
    return await apiGetActivity();
  } catch {
    return MOCK_ACTIVITY;
  }
}

// The Settings screen's Platforms / Model providers / Tools & MCP panels,
// backed by the real GET /api/status. Falls back to lib/mockData.ts's
// MOCK_STATUS (the same rows those panels used to hardcode directly).
export async function safeGetStatus(): Promise<SystemStatus> {
  try {
    return await apiGetStatus();
  } catch {
    return MOCK_STATUS;
  }
}

export interface DiffApprovalContext {
  conversationId: string;
  question: NonNullable<Message["approval"]>;
}

// AskQuestion.relatedTaskId (lib/api.ts) is now the real, explicit link
// between a diff's taskId and the approval question that gates merging it
// — this is a precise, direct lookup within the one conversation the diff
// screen already knows about (no cross-conversation scan needed, and no
// heuristic "first approval found in the same conversation" guess).
export async function getApprovalForDiff(conversationId: string, taskId: string): Promise<DiffApprovalContext | null> {
  const messages = await safeGetMessages(conversationId);
  const approvalMessage = messages.find((m) => m.approval?.relatedTaskId === taskId);
  if (approvalMessage?.approval) {
    return { conversationId, question: approvalMessage.approval };
  }
  return null;
}

export function personaOrYou(actor: string, personas: Map<string, Persona>): Persona | null {
  if (actor === "you") return null;
  return personas.get(actor) ?? null;
}

// No "pause every agent" endpoint exists in lib/api.ts yet — see the
// contract gap noted in the final report. Best-effort POST, swallowed on
// failure (no backend reachable) so the Activity screen's button stays
// visually functional rather than throwing.
export async function pauseAllAgents(): Promise<void> {
  const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
  try {
    await fetch(`${API_URL}/api/agents/pause-all`, { method: "POST", signal: AbortSignal.timeout(2000) });
  } catch {
    // no backend reachable — no-op
  }
}
