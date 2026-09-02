import { PersonaAvatar } from "@/components/persona/PersonaAvatar";
import type { Persona } from "@/lib/api";

export interface TypingIndicatorProps {
  personas: Persona[];
}

/** Renders nothing when `personas` is empty — callers don't need their own conditional. */
export function TypingIndicator({ personas }: TypingIndicatorProps) {
  if (personas.length === 0) return null;

  const names =
    personas.length === 1
      ? `${personas[0].name} is typing`
      : personas.length === 2
        ? `${personas[0].name} and ${personas[1].name} are typing`
        : `${personas.length} people are typing`;

  return (
    <div className="typing-indicator">
      <span className="typing-avatars">
        {personas.slice(0, 3).map((persona) => (
          <PersonaAvatar key={persona.id} persona={persona} size="sm" />
        ))}
      </span>
      <span className="typing-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="typing-label">{names}</span>
    </div>
  );
}
