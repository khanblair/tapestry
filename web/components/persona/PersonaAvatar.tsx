import type { Persona } from "@/lib/api";
import { StatusDot } from "./StatusDot";
import { UsersIcon } from "@/components/ui/icons";

export type AvatarSize = "sm" | "default" | "lg";

const SIZE_CLASS: Record<AvatarSize, string> = {
  sm: "sm",
  default: "",
  lg: "lg",
};

export interface PersonaAvatarProps {
  persona: Pick<Persona, "name" | "color" | "status">;
  size?: AvatarSize;
  /** Suppresses the status dot — used for the "You" avatar and group avatars, which have no persona status. */
  hideStatus?: boolean;
}

function initials(name: string): string {
  return name.slice(0, 1).toUpperCase();
}

/** Colored initial avatar with an optional status dot, matching the prototype's .avatar. */
export function PersonaAvatar({ persona, size = "default", hideStatus = false }: PersonaAvatarProps) {
  const classes = ["avatar", SIZE_CLASS[size]].filter(Boolean).join(" ");
  return (
    <div className={classes} style={{ background: persona.color }}>
      {initials(persona.name)}
      {!hideStatus && <StatusDot status={persona.status} />}
    </div>
  );
}

/** The "You" avatar — same shape as PersonaAvatar but for the local human user, who has no persona status. */
export function YouAvatar({ size = "default" }: { size?: AvatarSize }) {
  const classes = ["avatar", SIZE_CLASS[size]].filter(Boolean).join(" ");
  return (
    <div className={classes} style={{ background: "#475569" }}>
      Y
    </div>
  );
}

/** The gradient group-conversation avatar (prototype: linear-gradient(135deg,#3B82F6,#8B5CF6) with a users icon). Used where a specific conversation's members aren't the point — e.g. the "@all" mention option. */
export function GroupAvatar({ size = "default" }: { size?: AvatarSize }) {
  const classes = ["avatar", SIZE_CLASS[size]].filter(Boolean).join(" ");
  return (
    <div className={classes} style={{ background: "linear-gradient(135deg,#3B82F6,#8B5CF6)" }}>
      <UsersIcon size={size === "sm" ? 13 : 17} />
    </div>
  );
}

/**
 * Overlapping stack of each group conversation's actual member avatars
 * (Liquid Glass: real personas, not a generic group glyph) — falls back to
 * GroupAvatar when no members resolved yet (e.g. personas still loading).
 */
export function AvatarGroup({
  personas,
  size = "default",
  max = 3,
}: {
  personas: Pick<Persona, "name" | "color">[];
  size?: AvatarSize;
  max?: number;
}) {
  if (personas.length === 0) return <GroupAvatar size={size} />;
  const classes = ["avatar", SIZE_CLASS[size]].filter(Boolean).join(" ");
  return (
    <div className="avatar-group">
      {personas.slice(0, max).map((persona, i) => (
        <div key={i} className={classes} style={{ background: persona.color }}>
          {initials(persona.name)}
        </div>
      ))}
    </div>
  );
}
