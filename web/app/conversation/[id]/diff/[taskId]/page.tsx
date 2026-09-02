import { DiffScreenView } from "./DiffScreenView";

/**
 * Screen 10: the expanded diff/code review screen. Server wrapper unwrapping
 * Next.js 15's async `params` before handing plain strings down to the
 * client view — same split as app/profile/[personaId]/page.tsx.
 *
 * Lives as a plain nested route under app/conversation/[id]/ (part of the
 * `children` slot's own subtree, alongside page.tsx and default.tsx) — unlike
 * the thread screen, it doesn't need a parallel-route slot: the diff screen
 * fully replaces the conversation pane rather than sitting beside it, and
 * Modal.tsx's `position: fixed` covers the roster too, matching the
 * prototype's "modal over everything" look without needing `@modal`.
 */
export default async function DiffPage({
  params,
}: {
  params: Promise<{ id: string; taskId: string }>;
}) {
  const { id, taskId } = await params;
  return <DiffScreenView conversationId={id} taskId={taskId} />;
}
