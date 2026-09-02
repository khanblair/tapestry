"use client";

// Shared approval state across ApprovalCard (inline in a conversation),
// ApprovalActions (also embedded in the diff screen), and the Activity
// inbox's "Needs your input" section — approving/rejecting in any one of
// those three contexts must be reflected in the other two immediately.
//
// This is a plain module-scoped store (useSyncExternalStore), not a React
// context, deliberately: the three contexts live on three different routes,
// so a context would require reaching into the app-shell's root layout.tsx,
// which isn't this task's file to edit. A module store survives client-side
// route transitions the same way a context would; it just won't survive a
// hard reload — which is correct, since the real state should come back from
// the backend's GET on next load, not from client memory.

import { useSyncExternalStore } from "react";
import { answerAsk, type AskAnswer } from "./api";

export type ApprovalStatus = "pending" | "approved" | "rejected";

type Store = Record<string, ApprovalStatus>;

let state: Store = {};
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Store {
  return state;
}

function getServerSnapshot(): Store {
  return state;
}

/** Registers a question's initial status the first time it's seen, without clobbering a decision already made this session. */
export function seedApprovalStatus(questionId: string, status: ApprovalStatus = "pending") {
  if (!(questionId in state)) {
    state = { ...state, [questionId]: status };
    emit();
  }
}

export function getApprovalStatus(questionId: string): ApprovalStatus {
  return state[questionId] ?? "pending";
}

/** Subscribes a component to one question's status. Re-renders whenever any status changes (cheap enough at this scale; avoids a per-key subscription map). */
export function useApprovalStatus(questionId: string): ApprovalStatus {
  return useSyncExternalStore(
    subscribe,
    () => getApprovalStatus(questionId),
    () => getApprovalStatus(questionId)
  );
}

/**
 * Applies an approve/reject decision optimistically (so every mounted card
 * updates in the same tick), then calls answerAsk with the shared AskAnswer
 * shape the whole platform uses for approvals: `{ id, selected: ["approve"] }`
 * or `{ id, selected: ["reject"] }`. Reverts on failure.
 */
export async function decideApproval(
  conversationId: string,
  questionId: string,
  decision: "approve" | "reject"
): Promise<void> {
  const previous = state[questionId] ?? "pending";
  state = { ...state, [questionId]: decision === "approve" ? "approved" : "rejected" };
  emit();

  const answer: AskAnswer = { id: questionId, selected: [decision] };
  try {
    await answerAsk(conversationId, [answer]);
  } catch (err) {
    state = { ...state, [questionId]: previous };
    emit();
    throw err;
  }
}

/**
 * True if at least one of the given question ids is still "pending".
 * Used by the Activity screen's "Needs your input" section to decide
 * whether to show its empty state — approving/rejecting the last pending
 * item anywhere in the app (inline card, diff screen, or this same list)
 * flips this on the next store emit, same as useApprovalStatus.
 */
export function useAnyPending(questionIds: string[]): boolean {
  return useSyncExternalStore(
    subscribe,
    () => questionIds.some((id) => getApprovalStatus(id) === "pending"),
    () => questionIds.some((id) => getApprovalStatus(id) === "pending")
  );
}

/** Test/story escape hatch — resets the whole store. */
export function __resetApprovalsForTests() {
  state = {};
  emit();
}
