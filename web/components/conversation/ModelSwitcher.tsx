"use client";

// The conversation header's model control — tapestry_modes_models_personas_spec.md
// §2.2/§4. Shows the conversation's current effective model (Conversation.model)
// and, once a different model is picked, asks for a scope ("Just this
// message" = once, "This conversation" = session — global scope already has
// its own control on the Persona Management edit form and isn't offered
// here) before calling POST /api/conversations/{id}/model via
// setConversationModel (lib/api.ts).

import { useState } from "react";
import type { ModelSwitchScope } from "@/lib/api";
import { setConversationModel } from "@/lib/api";
import { MODEL_OPTIONS } from "@/lib/personaDetails";

export interface ModelSwitcherProps {
  conversationId: string;
  /** conversation.personaIds[0] — the lead persona, authoritative for conversation-level mode/model per the spec. */
  personaId: string;
  model: string;
  /** Called after a successful change so the caller (ConversationView) can update the conversation state it holds. */
  onModelChanged: (model: string) => void;
}

export function ModelSwitcher({ conversationId, personaId, model, onModelChanged }: ModelSwitcherProps) {
  // The model just picked in the <select>, awaiting a scope choice — not
  // yet sent to the backend. Cleared on a successful call, a cancel, or
  // re-picking the current model.
  const [pendingModel, setPendingModel] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleSelect(next: string) {
    setError(null);
    setPendingModel(next === model ? null : next);
  }

  async function confirmScope(scope: ModelSwitchScope) {
    if (!pendingModel || saving) return;
    setSaving(true);
    setError(null);
    try {
      await setConversationModel(conversationId, personaId, pendingModel, scope);
      onModelChanged(pendingModel);
      setPendingModel(null);
    } catch {
      setError("Couldn't change model — try again.");
    } finally {
      setSaving(false);
    }
  }

  // The current model is always a selectable option even if it's not in
  // MODEL_OPTIONS (e.g. a persona configured with a model string outside
  // this static list) -- otherwise the <select> would silently coerce to
  // whatever option happens to be first.
  const options =
    model && !MODEL_OPTIONS.some((opt) => opt.value === model)
      ? [{ value: model, label: model }, ...MODEL_OPTIONS]
      : MODEL_OPTIONS;

  return (
    <div className="model-switcher">
      <select
        className="input topbar-select"
        aria-label="Conversation model"
        value={pendingModel ?? model}
        disabled={saving}
        onChange={(event) => handleSelect(event.target.value)}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {pendingModel && (
        <div className="scope-picker">
          <div className="scope-label">Apply to</div>
          <button type="button" className="btn btn-sm" disabled={saving} onClick={() => void confirmScope("once")}>
            Just this message
          </button>
          <button type="button" className="btn btn-sm" disabled={saving} onClick={() => void confirmScope("session")}>
            This conversation
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={saving}
            onClick={() => setPendingModel(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {error && <span className="switcher-error">{error}</span>}
    </div>
  );
}
