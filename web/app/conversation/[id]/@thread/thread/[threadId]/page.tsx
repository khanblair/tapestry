import { notFound } from "next/navigation";
import { MessageBubble } from "@/components/conversation/MessageBubble";
import { Composer } from "@/components/conversation/Composer";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { getConversationById, getThreadMessages, safeGetPersonas } from "@/lib/safeApi";
import { ThreadTopbar } from "./ThreadTopbar";

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
          {messages.length === 0 && <div className="empty-hint">Nothing in this thread yet.</div>}
          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              actorPersona={message.actor === "you" ? undefined : personaById.get(message.actor)}
              renderApproval={(approval) => <ApprovalCard conversationId={message.conversationId} question={approval} />}
            />
          ))}
        </div>
      </div>

      <Composer conversationId={id} recipientName="thread" />
    </div>
  );
}
