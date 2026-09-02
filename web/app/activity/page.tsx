"use client";

// Screen 9: approvals inbox + running tasks + pause-all. Ports the
// prototype's activityScreen() — "Needs your input" (pending approvals),
// "Running now", "Recent", and a "Pause all agents" button — as a Modal
// screen (desktop centered / mobile full-cover, per Modal.tsx), same pattern
// as app/profile/[personaId]/PersonaProfileView.tsx.
//
// Contract gaps (no backend endpoint exists yet for any of these — see the
// final report):
//   - "Needs your input" is derived client-side from
//     lib/safeApi.ts's getPendingApprovals(), which scans every
//     conversation's messages for a `message.approval` field. A real
//     GET /api/asks?status=pending would be direct instead of a scan.
//   - "Running now" / "Recent" have no data source at all (no per-persona
//     activity feed, no "currently executing" task list). Ported verbatim
//     as static copy from the prototype, same as PersonaProfileView.tsx did
//     for its own "Recent activity" section.
//   - "Pause all agents" has no endpoint; lib/safeApi.ts's pauseAllAgents()
//     best-effort POSTs and swallows failure.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { ActivityBlock } from "@/components/conversation/ActivityBlock";
import { PauseIcon } from "@/components/ui/icons";
import { getPendingApprovals, pauseAllAgents, type PendingApproval } from "@/lib/safeApi";
import { useAnyPending, useApprovalStatus } from "@/lib/approvals";

/** Renders one pending approval's card, or nothing once it's been resolved (approved/rejected) this session — matching the prototype's activityScreen(), which drops a resolved approval from "Needs your input" entirely rather than showing a resolved-card variant here. */
function PendingApprovalItem({ approval }: { approval: PendingApproval }) {
  const status = useApprovalStatus(approval.question.id);
  if (status !== "pending") return null;
  return <ApprovalCard conversationId={approval.conversationId} question={approval.question} />;
}

export default function ActivityPage() {
  const router = useRouter();
  const [approvals, setApprovals] = useState<PendingApproval[] | null>(null);
  const [pausing, setPausing] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getPendingApprovals().then((found) => {
      if (!cancelled) setApprovals(found);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Re-renders the empty-state line once any approval resolves, even though
  // the fetched `approvals` array itself never changes — each item's own
  // pending/resolved state lives in lib/approvals.ts's shared store, not in
  // this array, since resolving one can happen from a different screen
  // entirely (the diff screen, or the inline card in the conversation).
  const anyPending = useAnyPending(approvals?.map((a) => a.question.id) ?? []);

  async function handlePauseAll() {
    setPausing(true);
    try {
      await pauseAllAgents();
      setPaused(true);
    } finally {
      setPausing(false);
    }
  }

  return (
    <Modal title="Activity" onClose={() => router.push("/")}>
      <div className="section-title">Needs your input</div>
      {approvals === null && <div className="empty-hint">Loading…</div>}
      {approvals !== null && !anyPending && <div className="empty-hint">Nothing waiting on you right now.</div>}
      {approvals?.map((approval) => (
        <PendingApprovalItem key={approval.question.id} approval={approval} />
      ))}

      <div className="section-title">Running now</div>
      <ActivityBlock label="Rex · running pytest tests/auth/" done={false} />

      <div className="section-title">Recent</div>
      <div style={{ fontSize: "12.5px", color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 9 }}>
        <div>Ada · proposed OAuth architecture · 30m ago</div>
        <div>Vex · flagged token scope issue · 8m ago</div>
      </div>

      <button
        type="button"
        className="btn btn-danger btn-block"
        style={{ marginTop: 18 }}
        disabled={pausing || paused}
        onClick={handlePauseAll}
      >
        <PauseIcon size={13} /> {paused ? "All agents paused" : pausing ? "Pausing…" : "Pause all agents"}
      </button>
    </Modal>
  );
}
