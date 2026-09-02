import { notFound } from "next/navigation";
import { safeGetConversations, safeGetMessages, safeGetPersonas, getPendingApprovals } from "@/lib/safeApi";
import { RosterList } from "@/components/roster/RosterList";
import { ConversationView } from "@/components/conversation/ConversationView";

export const dynamic = "force-dynamic";

interface ConversationPageProps {
  params: Promise<{ id: string }>;
}

/** The main chat pane (Screen 2), with the roster alongside it for the two-pane desktop/tablet layout. */
export default async function ConversationPage({ params }: ConversationPageProps) {
  const { id } = await params;

  // safeGet*/getPendingApprovals (lib/safeApi.ts) fall back to
  // lib/mockData.ts's fixtures when the backend isn't reachable — see
  // that file's header comment.
  const [personas, conversations, messages, pendingApprovals] = await Promise.all([
    safeGetPersonas(),
    safeGetConversations(),
    safeGetMessages(id),
    getPendingApprovals(),
  ]);

  const conversation = conversations.find((c) => c.id === id);
  if (!conversation) {
    notFound();
  }

  // Resolves the "OPEN CROSS-AGENT QUESTION" left in app/globals.css above
  // `.conv-thread-shell`: the thread screen took option (a), a real `@thread`
  // parallel-route slot in app/conversation/[id]/layout.tsx. That layout now
  // owns `.conv-thread-shell` + `.thread-slot` (wrapping this page's own
  // output as `children`, with the thread slot as a real sibling) — so this
  // page renders only the roster + conversation pane, no inert placeholder
  // `.thread-slot` of its own.
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
