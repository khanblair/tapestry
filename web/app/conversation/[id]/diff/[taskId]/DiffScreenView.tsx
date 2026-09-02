"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";
import { DiffViewer } from "@/components/diff/DiffViewer";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { getApprovalForDiff, getDiffDetail, type DiffApprovalContext } from "@/lib/safeApi";
import type { DiffDetail } from "@/lib/mockData";

export interface DiffScreenViewProps {
  conversationId: string;
  taskId: string;
}

/**
 * Ports the prototype's diffScreen() — file tabs, line-numbered diff,
 * `+142 -8` summary, and an Approve merge/Request changes action bar wired
 * to the same shared approval state as the inline card and the Activity
 * inbox — as a wide Modal screen. `wide` matches the prototype's
 * `{backTo:'convo:grp-auth', wide:true}`.
 */
export function DiffScreenView({ conversationId, taskId }: DiffScreenViewProps) {
  const router = useRouter();
  const [diff, setDiff] = useState<DiffDetail | null | undefined>(undefined);
  const [approval, setApproval] = useState<DiffApprovalContext | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getDiffDetail(taskId), getApprovalForDiff(taskId)]).then(([foundDiff, foundApproval]) => {
      if (!cancelled) {
        setDiff(foundDiff);
        setApproval(foundApproval);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  return (
    <Modal title={diff?.title ?? "Diff"} onClose={() => router.push(`/conversation/${conversationId}`)} wide>
      {diff === undefined && <div className="empty-hint">Loading…</div>}
      {diff === null && <div className="empty-hint">Diff not found.</div>}
      {diff && (
        <>
          <DiffViewer files={diff.files} additions={diff.additions} deletions={diff.deletions} fileCount={diff.fileCount} />
          <div style={{ display: "flex", gap: 8, padding: "14px 0 0", marginTop: 8, borderTop: "1px solid var(--border)" }}>
            {approval ? (
              <ApprovalActions
                conversationId={approval.conversationId}
                question={approval.question}
                approveLabel="Approve merge"
                rejectLabel="Request changes"
              />
            ) : (
              <span className="empty-hint">No pending approval is linked to this diff.</span>
            )}
          </div>
        </>
      )}
    </Modal>
  );
}
