import type { ReactNode } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AskQuestion, Message, Persona } from "@/lib/api";
import { PersonaAvatar, YouAvatar } from "@/components/persona/PersonaAvatar";
import { ActivityBlock } from "./ActivityBlock";
import { ClockTime } from "./ClockTime";
import { DiffChip } from "./DiffChip";
import { remarkMentions } from "@/lib/remarkMentions";

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
          <span className="msg-time">
            <ClockTime iso={message.timestamp} />
          </span>
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
