import type { ReactNode } from "react";

/**
 * Parallel-route layout for the conversation + thread three-pane behavior.
 *
 * `@thread` is a named parallel slot (app/conversation/[id]/@thread/) whose
 * only real route is thread/[threadId]/page.tsx. `children` is the implicit
 * slot resolved by page.tsx (the main conversation pane, once a sibling adds
 * it) or default.tsx (this segment's own fallback, used whenever the URL
 * goes deeper than `children`'s own subtree handles — i.e. whenever a thread
 * is open).
 *
 * On screens >=900px (app/globals.css `.thread-slot` + `.conv-thread-shell`)
 * both render side by side: the conversation pane stays visible with the
 * thread as a genuine third pane. Below 900px, `.thread-slot` becomes a
 * `position: fixed; inset: 0` full-cover overlay whenever it has content
 * (`:not(:empty)`), so opening a thread on mobile/tablet covers the
 * conversation exactly like the prototype's threadPanel(fullCover=true) —
 * no separate mobile-only route or client-side breakpoint branching needed,
 * it's the same component tree at every viewport, CSS-only visibility.
 *
 * IMPORTANT for whoever adds app/conversation/[id]/page.tsx: don't also add
 * app/conversation/[id]/thread/[threadId]/page.tsx as a plain nested route —
 * that would occupy the `children` slot at that URL instead of `@thread`,
 * replacing the conversation pane with the thread rather than showing both.
 * The thread page must live ONLY under the `@thread` slot.
 */
export default function ConversationLayout({
  children,
  thread,
}: {
  children: ReactNode;
  thread: ReactNode;
}) {
  return (
    <div className="conv-thread-shell">
      {children}
      <div className="thread-slot">{thread}</div>
    </div>
  );
}
