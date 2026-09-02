import type { Persona } from "@/lib/api";
import { PersonaAvatar, GroupAvatar } from "@/components/persona/PersonaAvatar";

/** One selectable row: either the synthetic "@all" entry or a real persona. */
export type MentionCandidate = { kind: "all" } | { kind: "persona"; persona: Persona };

export function mentionInsertText(candidate: MentionCandidate): string {
  return candidate.kind === "all" ? "all" : candidate.persona.id;
}

function mentionKey(candidate: MentionCandidate): string {
  return candidate.kind === "all" ? "all" : candidate.persona.id;
}

/**
 * Candidates for a conversation's persona list: every persona, plus a
 * synthetic "all" entry when there's more than one to fan out to (matches
 * `_resolve_mentions`'s own rule -- `@all` in a 1-persona DM is a no-op
 * duplicate of just mentioning that persona directly, so it's omitted
 * there rather than offered as a confusing extra option).
 */
export function mentionCandidates(personas: Persona[]): MentionCandidate[] {
  const list: MentionCandidate[] = personas.map((persona) => ({ kind: "persona", persona }));
  if (personas.length > 1) list.unshift({ kind: "all" });
  return list;
}

/**
 * Filters candidates by a typed prefix (the text after "@", so far) --
 * matches against a persona's id or name, or the literal "all", the same
 * three ways api.py's `_resolve_mentions` itself matches a handle.
 */
export function filterMentionCandidates(candidates: MentionCandidate[], query: string): MentionCandidate[] {
  const lowered = query.toLowerCase();
  if (!lowered) return candidates;
  return candidates.filter((candidate) => {
    if (candidate.kind === "all") return "all".startsWith(lowered);
    return candidate.persona.id.toLowerCase().startsWith(lowered) || candidate.persona.name.toLowerCase().startsWith(lowered);
  });
}

export interface MentionAutocompleteProps {
  candidates: MentionCandidate[];
  activeIndex: number;
  onSelect: (candidate: MentionCandidate) => void;
  onHover: (index: number) => void;
}

/** The "@"-triggered suggestion dropdown, anchored above the composer. */
export function MentionAutocomplete({ candidates, activeIndex, onSelect, onHover }: MentionAutocompleteProps) {
  return (
    <div className="mention-menu" role="listbox">
      {candidates.map((candidate, index) => {
        const key = mentionKey(candidate);
        const active = index === activeIndex;
        return (
          <button
            type="button"
            key={key}
            role="option"
            aria-selected={active}
            className={`mention-option${active ? " active" : ""}`}
            onMouseEnter={() => onHover(index)}
            onMouseDown={(event) => {
              // mousedown (not click) fires before the textarea blurs, so
              // selection survives and focus can return to the composer.
              event.preventDefault();
              onSelect(candidate);
            }}
          >
            {candidate.kind === "all" ? (
              <GroupAvatar size="sm" />
            ) : (
              <PersonaAvatar persona={candidate.persona} size="sm" />
            )}
            <span className="mention-option-label">
              {candidate.kind === "all" ? "all" : candidate.persona.name}
            </span>
            {candidate.kind === "all" && <span className="mention-option-sub">everyone in this conversation</span>}
            {candidate.kind === "persona" && <span className="mention-option-sub">{candidate.persona.role}</span>}
          </button>
        );
      })}
    </div>
  );
}
