"use client";

// The Approve/Reject button pair, split out from ApprovalCard specifically so
// it can be reused in three different contexts:
//   1. Inline inside ApprovalCard (a message in a conversation, or the
//      Activity screen's "Needs your input" list) — default "Approve"/"Reject"
//      labels.
//   2. The expanded diff screen's action bar — "Approve merge"/"Request
//      changes" labels (same buttons, different copy — the prototype's
//      diffScreen() uses different button text than the inline card, so the
//      labels are props, not hardcoded).
// Approving/rejecting in any one context updates the shared store
// (lib/approvals.ts) and therefore every other mounted instance for the same
// question id, in every context, on the same tick.
//
// When the question is already resolved, this swaps from the button pair to
// a small status chip in place — the diff screen relies on this swap
// happening in the same slot rather than the whole action bar disappearing.
// ApprovalCard, by contrast, replaces its *entire* card with a resolved
// variant and does not use this chip path (see ApprovalCard.tsx).

import { useState } from "react";
import type { AskQuestion } from "@/lib/api";
import { decideApproval, useApprovalStatus } from "@/lib/approvals";
import { CheckIcon, XIcon } from "@/components/ui/icons";

export interface ApprovalActionsProps {
  conversationId: string;
  question: Pick<AskQuestion, "id">;
  approveLabel?: string;
  rejectLabel?: string;
  size?: "sm" | "md";
  /** Called after a decision is applied (both optimistic update and the answerAsk call have settled). */
  onDecided?: (decision: "approve" | "reject") => void;
}

export function ApprovalActions({
  conversationId,
  question,
  approveLabel = "Approve",
  rejectLabel = "Reject",
  size = "sm",
  onDecided,
}: ApprovalActionsProps) {
  const status = useApprovalStatus(question.id);
  const [inFlight, setInFlight] = useState<"approve" | "reject" | null>(null);

  async function handle(decision: "approve" | "reject") {
    if (inFlight) return;
    setInFlight(decision);
    try {
      await decideApproval(conversationId, question.id, decision);
      onDecided?.(decision);
    } catch (err) {
      // decideApproval (lib/approvals.ts) already reverts the optimistic
      // update on failure and re-throws so a caller CAN react to it — but
      // this button's onClick invokes handle() fire-and-forget, so
      // without this catch the rethrow becomes an unhandled promise
      // rejection (surfaced here since there's no backend running in
      // this environment, so the answerAsk() call inside genuinely
      // fails every time). No UI treatment beyond the already-reverted
      // status swap is not exactly right for a real backend outage
      // either, but that's a product decision for whoever owns this
      // component, not this fix.
      console.error("Failed to record approval decision", err);
    } finally {
      setInFlight(null);
    }
  }

  if (status !== "pending") {
    const approved = status === "approved";
    return (
      <span
        className="chip"
        data-status={status}
        style={
          approved
            ? { background: "var(--accent-wash)", borderColor: "transparent", color: "var(--accent)" }
            : { color: "var(--danger)", borderColor: "var(--danger)" }
        }
      >
        {approved ? "Approved" : "Changes requested"}
      </span>
    );
  }

  const sizeClass = size === "sm" ? " btn-sm" : "";

  return (
    <div className="approval-actions">
      <button
        type="button"
        className={`btn btn-primary${sizeClass}`}
        disabled={inFlight !== null}
        aria-busy={inFlight === "approve"}
        onClick={() => handle("approve")}
      >
        <CheckIcon size={13} /> {approveLabel}
      </button>
      <button
        type="button"
        className={`btn btn-danger${sizeClass}`}
        disabled={inFlight !== null}
        aria-busy={inFlight === "reject"}
        onClick={() => handle("reject")}
      >
        <XIcon size={13} /> {rejectLabel}
      </button>
    </div>
  );
}
