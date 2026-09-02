"use client";

// Screen 9: approvals inbox + running tasks + pause-all. Ports the
// prototype's activityScreen() — "Needs your input" (pending approvals),
// "Running now", "Recent", and a "Pause all agents" button — as a Modal
// screen (desktop centered / mobile full-cover, per Modal.tsx), same pattern
// as app/profile/[personaId]/PersonaProfileView.tsx.
//
// "Needs your input" is backed by the real GET /api/asks/pending (via
// lib/safeApi.ts's getPendingApprovals()). "Running now" / "Recent" are
// backed by the real GET /api/activity (via lib/safeApi.ts's
// safeGetActivity()) instead of the static copy this screen used to render.
//
// Remaining contract gap: "Pause all agents" has no endpoint yet;
// lib/safeApi.ts's pauseAllAgents() best-effort POSTs and swallows failure.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { ActivityBlock } from "@/components/conversation/ActivityBlock";
import { PauseIcon } from "@/components/ui/icons";
import { getPendingApprovals, safeGetActivity, pauseAllAgents, type PendingApproval } from "@/lib/safeApi";
import type { ActivityFeed } from "@/lib/api";
import { useAnyPending, useApprovalStatus } from "@/lib/approvals";

/** "just now" / "Xm ago" / "Xh ago" from an ISO timestamp — good enough for
 * the Activity feed's "Recent" list without pulling in a date library. */
function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

/** Renders one pending approval's card, or nothing once it's been resolved (approved/rejected) this session — matching the prototype's activityScreen(), which drops a resolved approval from "Needs your input" entirely rather than showing a resolved-card variant here. */
function PendingApprovalItem({ approval }: { approval: PendingApproval }) {
  const status = useApprovalStatus(approval.question.id);
  if (status !== "pending") return null;
  return <ApprovalCard conversationId={approval.conversationId} question={approval.question} />;
}

export default function ActivityPage() {
  const router = useRouter();
  const [approvals, setApprovals] = useState<PendingApproval[] | null>(null);
  const [activity, setActivity] = useState<ActivityFeed | null>(null);
  const [pausing, setPausing] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getPendingApprovals().then((found) => {
      if (!cancelled) setApprovals(found);
    });
    safeGetActivity().then((found) => {
      if (!cancelled) setActivity(found);
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
      {activity !== null && activity.running.length === 0 && (
        <div className="empty-hint">Nothing running right now.</div>
      )}
      {activity?.running.map((item, i) => (
        <ActivityBlock key={`${item.conversationId}-${i}`} label={`${item.actor} · ${item.label}`} done={false} />
      ))}

      <div className="section-title">Recent</div>
      <div style={{ fontSize: "12.5px", color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 9 }}>
        {activity?.recent.map((item, i) => (
          <div key={`${item.conversationId}-${i}`}>
            {item.actor} · {item.label} · {relativeTime(item.timestamp)}
          </div>
        ))}
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
