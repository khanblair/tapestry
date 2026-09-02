import { safeGetConversations, safeGetPersonas, getPendingApprovals } from "@/lib/safeApi";
import { RosterList } from "@/components/roster/RosterList";

// Fetched fresh on every request — the roster/conversation list is live
// data from the backend, not something to statically cache at build time.
export const dynamic = "force-dynamic";

/** The app's home: the roster screen (Screen 1). */
export default async function RosterPage() {
  // safeGetPersonas/safeGetConversations (lib/safeApi.ts) fall back to
  // lib/mockData.ts's fixtures when the backend isn't reachable — see
  // that file's header comment for why: there's no backend running yet
  // in this environment, and every other screen already demos against
  // the same fallback rather than an empty page.
  const [personas, conversations, pendingApprovals] = await Promise.all([
    safeGetPersonas(),
    safeGetConversations(),
    getPendingApprovals(),
  ]);

  return (
    <div className="pane-shell" data-route="roster">
      <RosterList personas={personas} conversations={conversations} pendingApprovalCount={pendingApprovals.length} />
      <div className="conv-thread-shell">
        <div className="pane pane-conversation">
          <div className="topbar">
            <h2>Select a conversation</h2>
          </div>
          <div className="scroll">
            <div className="empty-hint screen-pad">Pick a DM or group from the left to see it here.</div>
          </div>
        </div>
        <div className="thread-slot" />
      </div>
    </div>
  );
}
