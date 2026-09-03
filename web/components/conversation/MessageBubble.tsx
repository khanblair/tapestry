"use client";

import { type ReactNode, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AskQuestion, Message, Persona } from "@/lib/api";
import { PersonaAvatar, YouAvatar } from "@/components/persona/PersonaAvatar";
import { ActivityBlock } from "./ActivityBlock";
import { ClockTime } from "./ClockTime";
import { DiffChip } from "./DiffChip";
import { remarkMentions } from "@/lib/remarkMentions";
import { EditIcon, ReplyIcon, SmileIcon, TrashIcon } from "@/components/ui/icons";

// Only loaded once a reaction picker is actually opened -- a full emoji
// data set has no business in the initial bundle for a screen that's
// mostly just text bubbles. next/dynamic + ssr:false since this is a
// client-only interactive widget (matches the general Next.js convention
// for large, browser-only libraries).
const EmojiPicker = dynamic(() => import("emoji-picker-react"), { ssr: false });

export interface MessageBubbleProps {
  message: Message;
  /**
   * The persona who sent this message, or undefined for the human user.
   * Convention (not encoded in the API types themselves — flagging this
   * for the backend to match): `message.actor === "you"` is the human;
   * any other value is a persona id looked up by the caller.
   */
  actorPersona?: Persona;
  /**
   * Extension point for approval-card rendering (components/approvals/,
   * not built in this pass). ConversationView doesn't import ApprovalCard
   * itself — pass a render function down from wherever it does exist so
   * MessageBubble never needs a hard dependency on a component it
   * doesn't own.
   */
  renderApproval?: (approval: AskQuestion) => ReactNode;
  /** The message `message.replyToId` points at, already resolved from the
   * loaded list by the caller (ConversationView) -- undefined if this
   * isn't a reply, or the target isn't loaded. */
  replyTarget?: Message;
  /** Display name for `replyTarget`'s actor ("You" or a persona's name). */
  replyTargetName?: string;
  /** Looks up a display name for a reaction's actor id ("you" or a persona id). */
  resolveActorName?: (actor: string) => string;
  onReply?: (message: Message) => void;
  onJumpToMessage?: (messageId: string) => void;
  onEdit?: (messageId: string, text: string) => Promise<void> | void;
  onDelete?: (messageId: string) => Promise<void> | void;
  onReact?: (messageId: string, emoji: string) => Promise<void> | void;
}

/**
 * Full markdown rendering for message text (bold/italic, lists, headers,
 * code blocks/spans, links, tables via remark-gfm) plus this app's own
 * `@handle` mention highlighting, via a custom remark plugin
 * (lib/remarkMentions.ts) that wraps each mention in a real `span.mention`
 * element at the AST level — see that file's own comment for why this
 * needs `data.hName`/`hProperties` rather than a `components` override —
 * so react-markdown renders it natively, no special-casing needed here.
 *
 * Replaces the previous plain-text `` `code` ``/`@mention`-only formatter:
 * LLM replies routinely include real markdown (headers, bullet lists,
 * bold text) that was rendering as literal `**text**`/`- item` before
 * this — found via real browser testing, not in the original scope doc.
 */
function renderMessageText(text: string) {
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkMentions]}>{text}</ReactMarkdown>;
}

function truncate(text: string, max = 80): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > max ? `${collapsed.slice(0, max)}…` : collapsed;
}

export function MessageBubble({
  message,
  actorPersona,
  renderApproval,
  replyTarget,
  replyTargetName,
  resolveActorName,
  onReply,
  onJumpToMessage,
  onEdit,
  onDelete,
  onReact,
}: MessageBubbleProps) {
  const isYou = message.actor === "you" || !actorPersona;
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(message.text);
  const [saving, setSaving] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  async function saveEdit() {
    const trimmed = editText.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    try {
      await onEdit?.(message.id, trimmed);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  const reactionGroups = new Map<string, string[]>();
  for (const reaction of message.reactions ?? []) {
    const actors = reactionGroups.get(reaction.emoji) ?? [];
    actors.push(reaction.actor);
    reactionGroups.set(reaction.emoji, actors);
  }

  if (message.deleted) {
    return (
      <div className="msg">
        {isYou ? <YouAvatar size="sm" /> : <PersonaAvatar persona={actorPersona} size="sm" />}
        <div className="body">
          <div className="msg-head">
            <span className="msg-name">{isYou ? "You" : actorPersona.name}</span>
            <span className="msg-time">
              <ClockTime iso={message.timestamp} />
            </span>
          </div>
          <div className="msg-deleted-text">This message was deleted.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="msg">
      {isYou ? <YouAvatar size="sm" /> : <PersonaAvatar persona={actorPersona} size="sm" />}
      <div className="body">
        <div className="msg-head">
          {isYou ? (
            <span className="msg-name">You</span>
          ) : (
            <Link href={`/profile/${actorPersona.id}`} className="msg-name clickable">
              {actorPersona.name}
            </Link>
          )}
          <span className="msg-time">
            <ClockTime iso={message.timestamp} />
          </span>
          {message.edited && <span className="msg-edited-tag">(edited)</span>}
        </div>

        {replyTarget && (
          <div className="msg-reply-quote" onClick={() => onJumpToMessage?.(replyTarget.id)}>
            <span className="who">{replyTargetName ?? replyTarget.actor}</span>
            <span className="snippet">
              {replyTarget.deleted ? "This message was deleted." : truncate(replyTarget.text)}
            </span>
          </div>
        )}

        {editing ? (
          <div className="msg-edit-box">
            <textarea
              className="field"
              rows={2}
              value={editText}
              disabled={saving}
              autoFocus
              onChange={(event) => setEditText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void saveEdit();
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  setEditing(false);
                  setEditText(message.text);
                }
              }}
            />
            <div className="actions">
              <button type="button" className="btn btn-sm btn-primary" disabled={saving} onClick={() => void saveEdit()}>
                Save
              </button>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                disabled={saving}
                onClick={() => {
                  setEditing(false);
                  setEditText(message.text);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="msg-text">{renderMessageText(message.text)}</div>
        )}

        {message.activity && (
          <ActivityBlock label={message.activity.label} done={message.activity.done} result={message.activity.result} />
        )}
        {message.diff && (
          <DiffChip
            conversationId={message.conversationId}
            taskId={message.diff.taskId}
            files={message.diff.files}
            add={message.diff.add}
            del={message.diff.del}
          />
        )}
        {message.approval && renderApproval?.(message.approval)}

        {reactionGroups.size > 0 && (
          <div className="reaction-bar">
            {Array.from(reactionGroups.entries()).map(([emoji, actors]) => (
              <button
                key={emoji}
                type="button"
                className={`reaction-chip${actors.includes("you") ? " mine" : ""}`}
                title={actors.map((actor) => resolveActorName?.(actor) ?? actor).join(", ")}
                onClick={() => void onReact?.(message.id, emoji)}
              >
                <span>{emoji}</span>
                <span className="count">{actors.length}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {!editing && (
        <div className={`msg-actions${pickerOpen ? " open" : ""}`}>
          {onReact && (
            <button
              type="button"
              className="msg-action-btn"
              aria-label="React"
              onClick={() => setPickerOpen((open) => !open)}
            >
              <SmileIcon size={15} />
            </button>
          )}
          {onReply && (
            <button type="button" className="msg-action-btn" aria-label="Reply" onClick={() => onReply(message)}>
              <ReplyIcon size={15} />
            </button>
          )}
          {isYou && onEdit && (
            <button type="button" className="msg-action-btn" aria-label="Edit" onClick={() => setEditing(true)}>
              <EditIcon size={14} />
            </button>
          )}
          {isYou && onDelete && (
            <button
              type="button"
              className="msg-action-btn danger"
              aria-label="Delete"
              onClick={() => {
                if (window.confirm("Delete this message?")) void onDelete(message.id);
              }}
            >
              <TrashIcon size={14} />
            </button>
          )}
        </div>
      )}

      {pickerOpen && (
        <div className="emoji-popover">
          <EmojiPicker
            reactionsDefaultOpen
            onEmojiClick={(data: { emoji: string }) => {
              void onReact?.(message.id, data.emoji);
              setPickerOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
