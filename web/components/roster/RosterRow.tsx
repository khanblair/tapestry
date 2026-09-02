import Link from "next/link";
import type { Conversation, Persona } from "@/lib/api";
import { GroupAvatar, PersonaAvatar } from "@/components/persona/PersonaAvatar";
import { RelativeTime } from "@/components/roster/RelativeTime";

export interface RosterRowProps {
  conversation: Conversation;
  /** All personas in the conversation, in order — RosterRow picks what it needs (first for a DM, all for a group name fallback). */
  personas: Persona[];
  active: boolean;
}

/** One row in the roster: a DM (single persona) or a group conversation. */
export function RosterRow({ conversation, personas, active }: RosterRowProps) {
  const isGroup = conversation.kind === "group";
  const primaryPersona = personas.find((p) => p.id === conversation.personaIds[0]);

  const name = isGroup
    ? conversation.name ?? "Group"
    : primaryPersona?.name ?? "Unknown persona";

  return (
    <Link href={`/conversation/${conversation.id}`} className={`roster-row${active ? " active" : ""}`}>
      {isGroup || !primaryPersona ? <GroupAvatar /> : <PersonaAvatar persona={primaryPersona} />}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="rname">{name}</div>
        {conversation.lastPreview && <div className="rprev">{conversation.lastPreview}</div>}
      </div>
      <div className="rtime">
        <RelativeTime iso={conversation.updatedAt} />
      </div>
    </Link>
  );
}
