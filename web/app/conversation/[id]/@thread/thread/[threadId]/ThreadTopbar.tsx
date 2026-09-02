"use client";

import { useRouter } from "next/navigation";
import { BackIcon, XIcon } from "@/components/ui/icons";

/**
 * The thread panel's topbar, as its own client component so the thread page
 * itself can stay an async Server Component for its data fetch — only the
 * close/back action needs client-side interactivity.
 *
 * The back/close buttons call router.back() rather than a plain
 * `<Link href={"/conversation/"+id}>` on purpose: Next.js parallel routes
 * preserve a slot's last-rendered state across a client-side navigation
 * whose target URL doesn't address that slot's own segment at all
 * (documented behavior — see the App Router parallel-routes docs' own
 * "closing a modal" example, which recommends exactly this fix). Pushing
 * forward to `/conversation/[id]` changes the URL but leaves the `@thread`
 * slot still showing the thread it just "closed"; router.back() replays the
 * exact prior history entry (the conversation, with no thread open), which
 * resolves correctly. Tried push()+router.refresh() as an alternative that
 * wouldn't depend on browser history — empirically it does NOT re-resolve
 * an already-rendered parallel slot back to its default.tsx either, so
 * back() is the one that actually works.
 *
 * This assumes a thread is always reached by an in-app forward navigation
 * FROM its parent conversation (the only way the product will let you open
 * one), so there's always a meaningful entry to go back to. A cold direct
 * load of a thread URL (no prior history at all) has nothing to go back to
 * either way — router.back() no-ops rather than erroring in that case.
 */
export function ThreadTopbar({ conversationId, parentLabel }: { conversationId: string; parentLabel: string }) {
  const router = useRouter();

  function close() {
    router.back();
  }

  return (
    <div className="topbar">
      <button type="button" className="icon-btn modal-back" aria-label="Back to conversation" onClick={close}>
        <BackIcon size={18} />
      </button>
      <h2>Thread</h2>
      <span className="sub">from {parentLabel}</span>
      <span className="spacer" />
      <button type="button" className="icon-btn modal-close" aria-label="Close thread" onClick={close}>
        <XIcon size={18} />
      </button>
    </div>
  );
}
