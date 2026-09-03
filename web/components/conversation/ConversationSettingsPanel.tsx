"use client";

// The conversation's "more info" screen -- opened from ConversationMenu's
// Settings item. Houses what used to live directly in the topbar (Mode/
// Model switchers) plus the ground-rules field from earlier today and a
// read-only member list. Archive/Delete stay on the menu itself rather
// than duplicated here, matching a chat app's usual split between a quick
// menu and a deeper settings screen.

import { useState } from "react";
import type { Conversation, Mode, Persona } from "@/lib/api";
import { setConversationContext } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";
import { PersonaAvatar } from "@/components/persona/PersonaAvatar";
import { ModeSwitcher } from "./ModeSwitcher";
import { ModelSwitcher } from "./ModelSwitcher";

export interface ConversationSettingsPanelProps {
  conversation: Conversation;
  members: Persona[];
  mode: Mode;
  model: string;
  onModeChanged: (mode: Mode) => void;
  onModelChanged: (model: string) => void;
  onClose: () => void;
}

export function ConversationSettingsPanel({
  conversation,
  members,
  mode,
  model,
  onModeChanged,
  onModelChanged,
  onClose,
}: ConversationSettingsPanelProps) {
  const [context, setContext] = useState(conversation.context ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const leadPersonaId = conversation.personaIds[0];

  async function saveContext() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await setConversationContext(conversation.id, context.trim());
      setSaved(true);
    } catch {
      setError("Couldn't save — try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="Conversation settings" onClose={onClose}>
      <div className="form-row">
        <label className="field-label">Mode</label>
        <ModeSwitcher conversationId={conversation.id} personaId={leadPersonaId} mode={mode} onModeChanged={onModeChanged} />
      </div>
      <div className="form-row">
        <label className="field-label">Model</label>
        <ModelSwitcher conversationId={conversation.id} personaId={leadPersonaId} model={model} onModelChanged={onModelChanged} />
      </div>
      <div className="form-row">
        <label className="field-label" htmlFor="settings-context">
          Ground rules / context
        </label>
        <textarea
          id="settings-context"
          className="textarea"
          rows={4}
          placeholder="e.g. Casual hangout only, no work talk. Keep replies short."
          value={context}
          onChange={(event) => {
            setContext(event.target.value);
            setSaved(false);
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
          <button type="button" className="btn btn-sm btn-primary" disabled={saving} onClick={() => void saveContext()}>
            {saving ? "Saving…" : "Save"}
          </button>
          {saved && <span style={{ fontSize: 12, color: "var(--accent)" }}>Saved.</span>}
          {error && <span className="switcher-error">{error}</span>}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
          Shown to every member above their own instructions — takes precedence if they conflict.
        </div>
      </div>

      <label className="field-label">
        {conversation.kind === "group" ? `Members (${members.length})` : "Persona"}
      </label>
      {members.map((persona) => (
        <div key={persona.id} className="list-item" style={{ cursor: "default" }}>
          <PersonaAvatar persona={persona} size="sm" />
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{persona.name}</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{persona.role}</div>
          </div>
        </div>
      ))}
    </Modal>
  );
}
