import { notFound } from "next/navigation";
import { safeGetConversations, safeGetMessages, safeGetPersonas, getPendingApprovals } from "@/lib/safeApi";
import { RosterList } from "@/components/roster/RosterList";
import { ConversationView } from "@/components/conversation/ConversationView";

export const dynamic = "force-dynamic";

/**
 * Fallback for the (implicit) children slot under app/conversation/[id]/.
 *
 * Needed once `@thread` exists as a sibling parallel slot: Next.js requires
 * every slot to resolve for every URL under this layout, including the
 * children slot. page.tsx handles `/conversation/[id]` directly; this file
 * is what renders instead whenever the URL goes deeper than that (i.e.
 * `/conversation/[id]/thread/[threadId]` — children has no page for that
 * path, so it falls back to here) — without it, opening a thread on a hard
 * reload/direct link 404s instead of showing the conversation pane beside
 * the thread.
 *
 * Deliberately mirrors page.tsx's roster+conversation shape exactly (same
 * data fetch, same <RosterList>/<ConversationView> composition), so the
 * desktop three-pane view looks identical whether a thread is open or not.
 * If page.tsx's shape changes, mirror the change here too — this is the
 * one piece of duplication the @thread slot design requires (see
 * app/conversation/[id]/layout.tsx's header comment for why a shared
 * children slot can't be reused as-is here).
 */
export default async function ConversationDefault({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const [personas, conversations, messages, pendingApprovals] = await Promise.all([
    safeGetPersonas(),
    safeGetConversations(),
    safeGetMessages(id),
    getPendingApprovals(),
  ]);

  const conversation = conversations.find((c) => c.id === id);
  if (!conversation) notFound();

  return (
    <div className="pane-shell" data-route="conversation">
      <RosterList
        personas={personas}
        conversations={conversations}
        activeConversationId={id}
        pendingApprovalCount={pendingApprovals.length}
      />
      <ConversationView conversation={conversation} personas={personas} initialMessages={messages} />
    </div>
  );
}
