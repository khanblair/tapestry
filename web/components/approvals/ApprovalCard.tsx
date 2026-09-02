"use client";

// The inline approve/reject card — used standalone inside a conversation's
// message list (via MessageBubble, when a message carries an `ask` with
// intent "approval") and again, unmodified, in the Activity screen's "Needs
// your input" section. Ports the prototype's `.approval-card` /
// `.approval-head` / `.approval-desc` markup and its three visual states
// (pending / approved / rejected) exactly.
//
// Unlike ApprovalActions (which swaps just its buttons for a status chip),
// ApprovalCard swaps its *entire* body: the pending state shows the shield
// icon + title + description + ApprovalActions; the resolved states show a
// checkmark/x header ("Approved by you" / "Changes requested") with just the
// question title, no actions — matching the prototype's resolved
// `.approval-card.resolved` variant.

import { useEffect } from "react";
import type { AskQuestion } from "@/lib/api";
import { seedApprovalStatus, useApprovalStatus } from "@/lib/approvals";
import { ApprovalActions } from "./ApprovalActions";
import { CheckIcon, ShieldIcon, XIcon } from "@/components/ui/icons";

export interface ApprovalCardProps {
  conversationId: string;
  question: AskQuestion;
}

export function ApprovalCard({ conversationId, question }: ApprovalCardProps) {
  const status = useApprovalStatus(question.id);

  // Register this question with the shared store the first time it's seen
  // (e.g. loaded from getConversations()/getPendingAsks() as still-pending),
  // without ever overwriting a decision already made this session.
  useEffect(() => {
    seedApprovalStatus(question.id, "pending");
  }, [question.id]);

  const resolved = status !== "pending";
  const rejected = status === "rejected";

  return (
    <div className={`approval-card${resolved ? " resolved" : ""}`} data-status={status} style={rejected ? { borderColor: "var(--danger)" } : undefined}>
      <div className="approval-head" style={rejected ? { color: "var(--danger)" } : undefined}>
        {status === "approved" && (
          <>
            <CheckIcon size={15} /> Approved by you
          </>
        )}
        {status === "rejected" && (
          <>
            <XIcon size={15} /> Changes requested
          </>
        )}
        {status === "pending" && (
          <>
            <ShieldIcon size={15} /> Needs your approval
          </>
        )}
      </div>
      <div className="approval-desc">
        {resolved ? (
          question.question
        ) : (
          <>
            <b style={{ color: "var(--text)" }}>{question.question}</b>
            {question.detail && (
              <>
                <br />
                {question.detail}
              </>
            )}
          </>
        )}
      </div>
      {!resolved && <ApprovalActions conversationId={conversationId} question={question} />}
    </div>
  );
}
