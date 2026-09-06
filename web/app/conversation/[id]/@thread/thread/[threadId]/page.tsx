import { notFound } from "next/navigation";
import { Composer } from "@/components/conversation/Composer";
import { getConversationById, getThreadMessages, safeGetPersonas } from "@/lib/safeApi";
import { ThreadTopbar } from "./ThreadTopbar";
import { ThreadMessageList } from "./ThreadMessageList";

/**
 * The spun-off thread view. Lives ONLY under the `@thread` parallel slot
 * (see app/conversation/[id]/layout.tsx) — never duplicate this at
 * app/conversation/[id]/thread/[threadId]/page.tsx as a plain route, which
 * would occupy the `children` slot instead and replace the conversation pane
 * rather than sitting beside it.
 *
 * Same component renders at every viewport; only CSS (`.thread-slot` /
 * `.pane-thread` in app/globals.css) decides whether this shows as a static
 * 320px third pane (>=900px, alongside the conversation) or a full-cover
 * overlay (below 900px, matching the prototype's threadPanel(fullCover)).
 * The back/close button pair mirrors Modal.tsx's `.modal-back`/`.modal-close`
 * convention on purpose — both link to the parent conversation URL, CSS picks
 * which one is visible per breakpoint, so there's exactly one navigation
 * target and no branch on viewport width in JS.
 *
 * Contract gap: lib/api.ts's sendMessage(conversationId, text) has no notion
 * of a thread — a reply sent here goes to the conversation's main message
 * stream, not scoped to this thread. Flagged in the final report; Composer
 * is still wired up so replying is visually functional, but the backend
 * needs a thread-scoped send (or a `threadId` on Message) before this is
 * actually correct.
 *
 * Message rendering itself lives in ./ThreadMessageList.tsx, a Client
 * Component — this file (a Server Component) can't render `MessageBubble`
 * directly with a `renderApproval` function prop, since React has no wire
 * format to send a function across the server/client boundary. Passing
 * `messages`/`personas` (plain, serializable data) across that boundary and
 * building the callback client-side, same as ConversationView.tsx already
 * does, is what actually works.
 */
export default async function ThreadPage({ params }: { params: Promise<{ id: string; threadId: string }> }) {
  const { id, threadId } = await params;

  const [conversation, personas, messages] = await Promise.all([
    getConversationById(id),
    safeGetPersonas(),
    getThreadMessages(id, threadId),
  ]);

  if (!conversation) notFound();

  const personaById = new Map(personas.map((p) => [p.id, p]));
  const parentLabel =
    conversation.kind === "group" ? conversation.name ?? conversation.id : personaById.get(conversation.personaIds[0])?.name ?? conversation.id;

  return (
    <div className="pane pane-thread">
      <ThreadTopbar conversationId={id} parentLabel={parentLabel} />

      <div className="scroll">
        <div className="msg-list">
          <ThreadMessageList messages={messages} personas={personas} />
        </div>
      </div>

      <Composer conversationId={id} recipientName="thread" />
    </div>
  );
}
