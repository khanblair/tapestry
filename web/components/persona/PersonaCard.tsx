import Link from "next/link";
import type { Persona } from "@/lib/api";
import { PersonaAvatar } from "@/components/persona/PersonaAvatar";
import { ChevronRightIcon } from "@/components/ui/icons";

export interface PersonaCardProps {
  persona: Persona;
  /** Overrides the default `/personas/{id}` edit-route destination. */
  href?: string;
}

/**
 * A single row in the persona management list (`app/personas/page.tsx`).
 * Distinct from `PersonaAvatar` (owned by the sibling agent, just the
 * circle+status-dot) -- this is the clickable list-item wrapper around it,
 * porting the `.list-item` row from the prototype's `personaMgmtScreen()`
 * list view: avatar, name, "role · model", chevron.
 */
export function PersonaCard({ persona, href }: PersonaCardProps) {
  return (
    <Link href={href ?? `/personas/${persona.id}`} className="list-item">
      <PersonaAvatar persona={persona} size="sm" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{persona.name}</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {persona.role} &middot; {persona.model}
        </div>
      </div>
      <ChevronRightIcon size={15} />
    </Link>
  );
}
