"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Conversation, Message, Persona } from "@/lib/api";
import { subscribeToConversation } from "@/lib/api";
import { PersonaAvatar, GroupAvatar } from "@/components/persona/PersonaAvatar";
import { StatusPill } from "@/components/persona/StatusDot";
import { BackIcon, DotsIcon, FolderIcon } from "@/components/ui/icons";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";

export interface ConversationViewProps {
  conversation: Conversation;
  personas: Persona[];
  initialMessages: Message[];
}

/**
 * The main chat pane: topbar (persona/group identity + entry points to
 * profile/diff), live message list, and composer. Subscribes to the
 * conversation's WebSocket stream for new events and appends them as
 * they arrive.
 */
export function ConversationView({ conversation, personas, initialMessages }: ConversationViewProps) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages(initialMessages);
  }, [conversation.id, initialMessages]);

  useEffect(() => {
    const unsubscribe = subscribeToConversation(conversation.id, (event) => {
      // The backend's event shape beyond {type, payload} isn't finalized
      // yet — "message" is the one event type we know we need to handle
      // (a new Message arriving live). Anything else is ignored rather
      // than guessed at.
      if (event.type === "message" && event.payload) {
        setMessages((prev) => [...prev, event.payload as Message]);
      }
    });
    return unsubscribe;
  }, [conversation.id]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const personaById = useMemo(() => new Map(personas.map((p) => [p.id, p])), [personas]);

  const isGroup = conversation.kind === "group";
  const groupPersonas = isGroup
    ? conversation.personaIds.map((id) => personaById.get(id)).filter((p): p is Persona => Boolean(p))
    : [];
  const primaryPersona = !isGroup ? personaById.get(conversation.personaIds[0]) : undefined;
  const headerName = isGroup ? conversation.name ?? "Group" : primaryPersona?.name ?? "Unknown persona";

  // The group header's "open diff" shortcut points at the most recent
  // message that actually carries a diff, since Conversation has no
  // "current task" concept of its own. If no message has a diff yet,
  // the button is omitted rather than linking somewhere invalid.
  const latestDiff = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].diff) return messages[i].diff;
    }
    return undefined;
  }, [messages]);
  // NOTE: there is no header shortcut to a specific thread here — Message
  // carries no threadId, so ConversationView has no way to route to a
  // concrete /conversation/[id]/thread/[threadId]. Whoever owns the
  // thread screen should add that field (additive) if a header entry
  // point into "the" active thread is wanted; this is a known gap, not
  // an oversight.

  return (
    <div className="pane pane-conversation">
      <div className="topbar">
        <Link href="/" className="icon-btn conv-back-btn" aria-label="Back to conversations">
          <BackIcon size={18} />
        </Link>

        {isGroup ? (
          <>
            <GroupAvatar size="sm" />
            <div>
              <h2>{headerName}</h2>
              <div className="sub">{groupPersonas.map((p) => p.name).join(", ")}</div>
            </div>
            <span className="spacer" />
            {latestDiff && (
              <Link href={`/conversation/${conversation.id}/diff/${latestDiff.taskId}`} className="icon-btn" aria-label="View diff">
                <FolderIcon size={18} />
              </Link>
            )}
          </>
        ) : primaryPersona ? (
          <>
            <PersonaAvatar persona={primaryPersona} size="sm" />
            <div>
              <Link href={`/profile/${primaryPersona.id}`}>
                <h2 style={{ cursor: "pointer" }}>{primaryPersona.name}</h2>
              </Link>
              <StatusPill status={primaryPersona.status} label={`${statusLabel(primaryPersona.status)} · ${primaryPersona.model}`} />
            </div>
            <span className="spacer" />
            <Link href={`/profile/${primaryPersona.id}`} className="icon-btn" aria-label="Persona details">
              <DotsIcon size={18} />
            </Link>
          </>
        ) : (
          <h2>Select a conversation</h2>
        )}
      </div>

      <div className="scroll" ref={scrollRef}>
        <div className="msg-list">
          {messages.length === 0 && (
            <div className="empty-hint">
              This is the start of {isGroup ? headerName : `your DM with ${headerName}`}.
            </div>
          )}
          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              actorPersona={message.actor === "you" ? undefined : personaById.get(message.actor)}
              renderApproval={(approval) => <ApprovalCard conversationId={conversation.id} question={approval} />}
            />
          ))}
        </div>
      </div>

      <Composer
        conversationId={conversation.id}
        recipientName={headerName}
        onSent={(message) => setMessages((prev) => [...prev, message])}
      />
    </div>
  );
}

function statusLabel(status: Persona["status"]): string {
  return { online: "Online", busy: "Working", paused: "Paused", offline: "Offline" }[status];
}
