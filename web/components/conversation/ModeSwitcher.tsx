"use client";

// The conversation header's mode control — tapestry_modes_models_personas_spec.md
// §1.6/§4. Shows the conversation's current effective mode (Conversation.mode,
// the lead persona's most recent mode/changed event or its default_mode) and,
// on selection, posts POST /api/conversations/{id}/mode via
// setConversationMode (lib/api.ts). No dedicated dropdown/menu primitive
// exists in components/ui/ yet (Modal is a full-screen overlay, Toggle is a
// boolean switch, PersonaEditForm's own "Model" field is a plain
// `<select className="input">`) — this reuses that same native-select
// pattern rather than inventing a bespoke popover component, just sized to
// fit the topbar via the `.topbar-select` class (app/globals.css).

import { useState } from "react";
import type { Mode } from "@/lib/api";
import { setConversationMode } from "@/lib/api";
import { MODE_OPTIONS } from "@/lib/personaDetails";

export interface ModeSwitcherProps {
  conversationId: string;
  /** conversation.personaIds[0] — the lead persona, authoritative for conversation-level mode/model per the spec. */
  personaId: string;
  mode: Mode;
  /** Called after a successful change so the caller (ConversationView) can update the conversation state it holds. */
  onModeChanged: (mode: Mode) => void;
}

export function ModeSwitcher({ conversationId, personaId, mode, onModeChanged }: ModeSwitcherProps) {
  const [saving, setSaving] = useState(false);
  // A real backend call that can fail (unlike lib/safeApi.ts's best-effort
  // pauseAllAgents, which silently no-ops) — this changes what a persona is
  // allowed to do without asking, so a failure is surfaced inline rather
  // than swallowed, matching PersonaEditForm's own `saveError` pattern
  // (the only precedent for user-facing error text in this codebase).
  const [error, setError] = useState<string | null>(null);

  async function handleChange(next: Mode) {
    if (next === mode || saving) return;
    setSaving(true);
    setError(null);
    try {
      await setConversationMode(conversationId, personaId, next);
      onModeChanged(next);
    } catch {
      setError("Couldn't change mode — try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mode-switcher">
      <select
        className="input topbar-select"
        aria-label="Conversation mode"
        value={mode}
        disabled={saving}
        onChange={(event) => void handleChange(event.target.value as Mode)}
      >
        {MODE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value} title={opt.description}>
            {opt.label}
          </option>
        ))}
      </select>
      {error && <span className="switcher-error">{error}</span>}
    </div>
  );
}
