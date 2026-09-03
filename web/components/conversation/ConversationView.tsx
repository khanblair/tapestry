"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Conversation, Message, Mode, Persona } from "@/lib/api";
import {
  deleteMessage,
  editMessage,
  reactToMessage,
  subscribeToConversation,
  stopConversation,
} from "@/lib/api";
import { PersonaAvatar, GroupAvatar } from "@/components/persona/PersonaAvatar";
import { StatusPill } from "@/components/persona/StatusDot";
import { ArrowDownIcon, BackIcon, FolderIcon, StopIcon } from "@/components/ui/icons";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { ConversationMenu } from "./ConversationMenu";
import { ConversationSettingsPanel } from "./ConversationSettingsPanel";
import { TypingIndicator } from "./TypingIndicator";

export interface ConversationViewProps {
  conversation: Conversation;
  personas: Persona[];
  initialMessages: Message[];
}

// How close to the bottom (px of unscrolled content below the viewport)
// still counts as "at the bottom" for auto-scroll purposes -- matches the
// usual chat-app convention of a generous-but-not-huge threshold, not a
// pixel-perfect check.
const NEAR_BOTTOM_THRESHOLD_PX = 80;

function isNearBottom(el: HTMLDivElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD_PX;
}

/**
 * The main chat pane: topbar (persona/group identity + entry points to
 * profile/diff/settings), live message list, and composer. Subscribes to
 * the conversation's WebSocket stream for new events and appends them as
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
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const wasNearBottomRef = useRef(true);

  const [showScrollButton, setShowScrollButton] = useState(false);
  const [unseenCount, setUnseenCount] = useState(0);
  const [replyingTo, setReplyingTo] = useState<Message | null>(null);
  const [archived, setArchived] = useState(conversation.archived);
  const [settingsOpen, setSettingsOpen] = useState(false);

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
    setArchived(conversation.archived);
  }, [conversation.id, conversation.mode, conversation.model, conversation.archived]);

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
      } else if (
        (event.type === "message/edited" || event.type === "message/deleted" || event.type === "message/reacted") &&
        event.payload
      ) {
        // These three always target an EXISTING message -- merge by id
        // (replace, never append) rather than the append-if-new logic
        // above. The endpoint that triggered this already updated local
        // state from its own HTTP response, so a duplicate arrival here
        // is a harmless no-op overwrite with identical data.
        const updated = event.payload as Message;
        setMessages((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
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

  // Single source of truth for the button's visibility, called from BOTH
  // the native scroll listener and every time the message list itself
  // changes -- found live: driving `showScrollButton` off scroll events
  // alone let it go stale (most visibly, never NEEDS scrollable overflow
  // to show at all, e.g. right after opening a short conversation, before
  // any real user scroll had fired even once to establish ground truth).
  // hasOverflow guards the other direction: never show it when there's
  // nothing to scroll to in the first place.
  function syncScrollButtonState(el: HTMLDivElement) {
    const hasOverflow = el.scrollHeight > el.clientHeight + NEAR_BOTTOM_THRESHOLD_PX;
    const nearBottom = isNearBottom(el);
    wasNearBottomRef.current = nearBottom;
    setShowScrollButton(hasOverflow && !nearBottom);
    if (nearBottom) setUnseenCount(0);
  }

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (wasNearBottomRef.current) {
      el.scrollTop = el.scrollHeight;
      setUnseenCount(0);
      setShowScrollButton(false);
    } else {
      setUnseenCount((count) => count + 1);
      syncScrollButtonState(el);
    }
  }, [messages, typingPersonaIds]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    function handleScroll() {
      syncScrollButtonState(el as HTMLDivElement);
    }
    el.addEventListener("scroll", handleScroll);
    handleScroll();
    return () => el.removeEventListener("scroll", handleScroll);
  }, [conversation.id]);

  function scrollToBottom() {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setUnseenCount(0);
  }

  function jumpToMessage(messageId: string) {
    const target = messageRefs.current.get(messageId);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

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
  const messageById = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages]);
  const resolveActorName = (actor: string) => (actor === "you" ? "You" : personaById.get(actor)?.name ?? actor);

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
  const settingsMembers = isGroup ? groupPersonas : primaryPersona ? [primaryPersona] : [];

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

  async function handleEdit(messageId: string, text: string) {
    const updated = await editMessage(conversation.id, messageId, text);
    setMessages((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
  }

  async function handleDelete(messageId: string) {
    const updated = await deleteMessage(conversation.id, messageId);
    setMessages((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
  }

  async function handleReact(messageId: string, emoji: string) {
    const updated = await reactToMessage(conversation.id, messageId, emoji);
    setMessages((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
  }

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
            <ConversationMenu
              conversationId={conversation.id}
              archived={archived}
              onArchivedChanged={setArchived}
              onOpenSettings={() => setSettingsOpen(true)}
            />
          </>
        ) : primaryPersona ? (
          <>
            <PersonaAvatar persona={primaryPersona} size="sm" />
            <div>
              <Link href={`/profile/${primaryPersona.id}`}>
                <h2 style={{ cursor: "pointer" }}>{primaryPersona.name}</h2>
              </Link>
              <StatusPill status={primaryPersona.status} label={statusLabel(primaryPersona.status)} />
            </div>
            <span className="spacer" />
            <ConversationMenu
              conversationId={conversation.id}
              archived={archived}
              onArchivedChanged={setArchived}
              onOpenSettings={() => setSettingsOpen(true)}
            />
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
          {messages.map((message) => {
            const replyTarget = message.replyToId ? messageById.get(message.replyToId) : undefined;
            return (
              <div key={message.id} ref={(el) => {
                if (el) messageRefs.current.set(message.id, el);
                else messageRefs.current.delete(message.id);
              }}>
                <MessageBubble
                  message={message}
                  actorPersona={message.actor === "you" ? undefined : personaById.get(message.actor)}
                  renderApproval={(approval) => <ApprovalCard conversationId={conversation.id} question={approval} />}
                  replyTarget={replyTarget}
                  replyTargetName={replyTarget ? resolveActorName(replyTarget.actor) : undefined}
                  resolveActorName={resolveActorName}
                  onReply={setReplyingTo}
                  onJumpToMessage={jumpToMessage}
                  onEdit={handleEdit}
                  onDelete={handleDelete}
                  onReact={handleReact}
                />
              </div>
            );
          })}
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
        {showScrollButton && (
          <button type="button" className="scroll-to-bottom-btn" aria-label="Scroll to latest" onClick={scrollToBottom}>
            <ArrowDownIcon size={17} />
            {unseenCount > 0 && <span className="scroll-to-bottom-badge">{Math.min(unseenCount, 9)}</span>}
          </button>
        )}
      </div>

      <Composer
        conversationId={conversation.id}
        recipientName={headerName}
        personas={conversationPersonas}
        replyingTo={replyingTo ? { message: replyingTo, actorName: resolveActorName(replyingTo.actor) } : undefined}
        onCancelReply={() => setReplyingTo(null)}
        onSent={(message) =>
          setMessages((prev) => (prev.some((m) => m.id === message.id) ? prev : [...prev, message]))
        }
      />

      {settingsOpen && (
        <ConversationSettingsPanel
          conversation={{ ...conversation, mode, model, archived }}
          members={settingsMembers}
          mode={mode}
          model={model}
          onModeChanged={setMode}
          onModelChanged={setModel}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}

function statusLabel(status: Persona["status"]): string {
  return { online: "Online", busy: "Working", paused: "Paused", offline: "Offline" }[status];
}
