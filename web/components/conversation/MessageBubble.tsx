import type { ReactNode } from "react";
import Link from "next/link";
import type { AskQuestion, Message, Persona } from "@/lib/api";
import { PersonaAvatar, YouAvatar } from "@/components/persona/PersonaAvatar";
import { ActivityBlock } from "./ActivityBlock";
import { DiffChip } from "./DiffChip";
import { formatClockTime } from "@/lib/time";

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
}

/** Very small inline formatter for the prototype's two message-text conventions: `@Mention` and `` `code` ``. */
function renderMessageText(text: string) {
  const parts = text.split(/(`[^`]+`|@\w+)/g);
  return parts.map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={i}>{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("@")) {
      return (
        <span key={i} className="mention">
          {part}
        </span>
      );
    }
    return part;
  });
}

export function MessageBubble({ message, actorPersona, renderApproval }: MessageBubbleProps) {
  const isYou = message.actor === "you" || !actorPersona;

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
          {!isYou && <span className="msg-role">&middot; {actorPersona.role}</span>}
          <span className="msg-time">{formatClockTime(message.timestamp)}</span>
        </div>
        <div className="msg-text">{renderMessageText(message.text)}</div>
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
      </div>
    </div>
  );
}
