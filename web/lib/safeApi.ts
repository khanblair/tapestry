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
  type Conversation,
  type Message,
  type Persona,
} from "./api";
import { MOCK_CONVERSATIONS, MOCK_MESSAGES, MOCK_PERSONAS, MOCK_THREAD_MESSAGES, type DiffDetail, MOCK_DIFFS } from "./mockData";

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

// No backend endpoint exists yet for fetching a diff's full line-level
// content by taskId (Message.diff only carries summary counts) — see the
// contract gap noted in the final report. Falls back to lib/mockData.ts's
// MOCK_DIFFS.
export async function getDiffDetail(taskId: string): Promise<DiffDetail | null> {
  return MOCK_DIFFS[taskId] ?? null;
}

export interface PendingApproval {
  conversationId: string;
  conversationLabel: string;
  question: NonNullable<Message["approval"]>;
}

// The Activity screen's "Needs your input" section needs every pending
// approval-intent ask across all conversations. No backend endpoint exists
// yet for this (e.g. GET /api/asks?status=pending) — see the contract gap
// noted in the final report. Scans each conversation's messages for an
// `approval` field instead.
export async function getPendingApprovals(): Promise<PendingApproval[]> {
  const [conversations] = await Promise.all([safeGetConversations()]);
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

export interface DiffApprovalContext {
  conversationId: string;
  question: NonNullable<Message["approval"]>;
}

// Contract gap: there's no explicit link between a diff's taskId and the
// approval question that gates merging it — Message.diff and
// Message.approval are two independent optional fields that can land on
// different messages. Heuristic used here: find the conversation containing
// a message whose diff.taskId matches, then return the first approval-intent
// question found anywhere in that same conversation. Works for the one
// diff/approval pair in lib/mockData.ts's fixtures; a real backend should
// carry this link explicitly (e.g. the approval's payload referencing the
// taskId it gates) rather than requiring a scan.
export async function getApprovalForDiff(taskId: string): Promise<DiffApprovalContext | null> {
  const conversations = await safeGetConversations();
  for (const convo of conversations) {
    const messages = await safeGetMessages(convo.id);
    if (!messages.some((m) => m.diff?.taskId === taskId)) continue;
    const approvalMessage = messages.find((m) => m.approval);
    if (approvalMessage?.approval) {
      return { conversationId: convo.id, question: approvalMessage.approval };
    }
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
