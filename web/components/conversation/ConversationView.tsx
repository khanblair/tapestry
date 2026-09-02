"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Conversation, Message, Mode, Persona } from "@/lib/api";
import { subscribeToConversation, stopConversation } from "@/lib/api";
import { PersonaAvatar, GroupAvatar } from "@/components/persona/PersonaAvatar";
import { StatusPill } from "@/components/persona/StatusDot";
import { BackIcon, DotsIcon, FolderIcon, StopIcon } from "@/components/ui/icons";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { ModeSwitcher } from "./ModeSwitcher";
import { ModelSwitcher } from "./ModelSwitcher";
import { TypingIndicator } from "./TypingIndicator";

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
  // Persona ids currently mid-turn, per the persona/typing WS frame
  // (api.py's _drive_turn) -- a Set so a tag-all fan-out with several
  // personas typing at once tracks each independently, and adding/
  // removing the same id twice is a no-op rather than a bug.
  const [typingPersonaIds, setTypingPersonaIds] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  // The lead persona's effective mode/model, held locally so the switcher
  // controls below can update immediately on a successful backend call
  // without a full page refetch — mirrors how `messages` above is updated
  // optimistically off WebSocket events rather than re-fetched. Reset
  // whenever the conversation itself changes (navigating to a different
  // conversation), same as `messages`.
  const [mode, setMode] = useState<Mode>(conversation.mode);
  const [model, setModel] = useState<string>(conversation.model);

  useEffect(() => {
    setMessages(initialMessages);
  }, [conversation.id, initialMessages]);

  useEffect(() => {
    setMode(conversation.mode);
    setModel(conversation.model);
  }, [conversation.id, conversation.mode, conversation.model]);

  useEffect(() => {
    // A stale indicator from the previous conversation must not survive
    // navigating to a new one -- there's no "typing" WS frame telling us
    // to clear it on unmount, since the old socket is simply closed.
    setTypingPersonaIds(new Set());

    const unsubscribe = subscribeToConversation(conversation.id, (event) => {
      if (event.type === "message" && event.payload) {
        const incoming = event.payload as Message;
        // The backend broadcasts every message it appends over this
        // socket, including the human's own (see api.py's
        // _broadcast_new_messages) -- Composer's onSent below already
        // appended that same message optimistically for instant
        // feedback, so without this id check it renders twice once the
        // WS frame for it arrives.
        setMessages((prev) => (prev.some((m) => m.id === incoming.id) ? prev : [...prev, incoming]));
      } else if (event.type === "persona/typing" && event.payload) {
        const { persona_id: personaId, done } = event.payload as { persona_id: string; done?: boolean };
        setTypingPersonaIds((prev) => {
          const next = new Set(prev);
          if (done) next.delete(personaId);
          else next.add(personaId);
          return next;
        });
      }
    });
    return unsubscribe;
  }, [conversation.id]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, typingPersonaIds]);

  const [stopping, setStopping] = useState(false);
  async function handleStop() {
    setStopping(true);
    try {
      await stopConversation(conversation.id);
    } catch (error) {
      console.error("Failed to stop generation", error);
    } finally {
      setStopping(false);
    }
  }

  const personaById = useMemo(() => new Map(personas.map((p) => [p.id, p])), [personas]);
  const typingPersonas = useMemo(
    () =>
      Array.from(typingPersonaIds)
        .map((id) => personaById.get(id))
        .filter((p): p is Persona => Boolean(p)),
    [typingPersonaIds, personaById],
  );

  const isGroup = conversation.kind === "group";
  const groupPersonas = isGroup
    ? conversation.personaIds.map((id) => personaById.get(id)).filter((p): p is Persona => Boolean(p))
    : [];
  const primaryPersona = !isGroup ? personaById.get(conversation.personaIds[0]) : undefined;
  // Composer's "@"-autocomplete candidate list: every persona actually in
  // this conversation, DM or group alike.
  const conversationPersonas = isGroup ? groupPersonas : primaryPersona ? [primaryPersona] : [];
  const headerName = isGroup ? conversation.name ?? "Group" : primaryPersona?.name ?? "Unknown persona";
  // The lead persona (personaIds[0]) is authoritative for conversation-level
  // mode/model state for both a DM and a group — same convention the rest
  // of this app already uses (tapestry_modes_models_personas_spec.md §1.6).
  const leadPersonaId = conversation.personaIds[0];

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
            <ModeSwitcher conversationId={conversation.id} personaId={leadPersonaId} mode={mode} onModeChanged={setMode} />
            <ModelSwitcher conversationId={conversation.id} personaId={leadPersonaId} model={model} onModelChanged={setModel} />
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
              {/* `model` (local state, seeded from conversation.model) — not
                  primaryPersona.model — since Conversation.model is the
                  documented effective model (lib/api.ts), which can diverge
                  from the persona's own configured model after a session-
                  scoped switch via ModelSwitcher below. Using the persona's
                  static field here would show a second, stale answer to
                  "what model is this conversation running?" right next to
                  the switcher that just changed it. */}
              <StatusPill status={primaryPersona.status} label={`${statusLabel(primaryPersona.status)} · ${model}`} />
            </div>
            <span className="spacer" />
            <ModeSwitcher conversationId={conversation.id} personaId={leadPersonaId} mode={mode} onModeChanged={setMode} />
            <ModelSwitcher conversationId={conversation.id} personaId={leadPersonaId} model={model} onModelChanged={setModel} />
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
          {typingPersonas.length > 0 && (
            <div className="typing-row">
              <TypingIndicator personas={typingPersonas} />
              <button
                type="button"
                className="btn btn-sm btn-danger"
                onClick={handleStop}
                disabled={stopping}
                aria-label="Stop generating"
              >
                <StopIcon size={13} /> Stop
              </button>
            </div>
          )}
        </div>
      </div>

      <Composer
        conversationId={conversation.id}
        recipientName={headerName}
        personas={conversationPersonas}
        onSent={(message) =>
          setMessages((prev) => (prev.some((m) => m.id === message.id) ? prev : [...prev, message]))
        }
      />
    </div>
  );
}

function statusLabel(status: Persona["status"]): string {
  return { online: "Online", busy: "Working", paused: "Paused", offline: "Offline" }[status];
}
