"use client";

import { MessageBubble } from "@/components/conversation/MessageBubble";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import type { Message, Persona } from "@/lib/api";

/**
 * Split out of page.tsx (a Server Component) because `MessageBubble`'s
 * `renderApproval` prop is a function, and a Server Component cannot pass a
 * function directly to a Client Component -- React's RSC serialization has
 * no wire format for it ("Functions cannot be passed directly to Client
 * Components..."). ConversationView.tsx never hits this because it's
 * itself `"use client"`, so its own inline `renderApproval` stays entirely
 * within client-to-client rendering. This component exists purely to move
 * that same construction across the boundary: page.tsx passes it plain,
 * serializable data (messages, personas), and the function itself is built
 * here, client-side, same as ConversationView does.
 */
export function ThreadMessageList({ messages, personas }: { messages: Message[]; personas: Persona[] }) {
  const personaById = new Map(personas.map((p) => [p.id, p]));

  if (messages.length === 0) {
    return <div className="empty-hint">Nothing in this thread yet.</div>;
  }

  return (
    <>
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          actorPersona={message.actor === "you" ? undefined : personaById.get(message.actor)}
          renderApproval={(approval) => <ApprovalCard conversationId={message.conversationId} question={approval} />}
        />
      ))}
    </>
  );
}
