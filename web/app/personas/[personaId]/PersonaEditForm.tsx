"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createPersona, updatePersona, type Persona } from "@/lib/api";
import { getPersonaById } from "@/lib/safeApi";
import { MODEL_OPTIONS, TOOL_OPTIONS, NEW_PERSONA_COLOR } from "@/lib/personaDetails";
import { CheckIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/Modal";

export interface PersonaEditFormProps {
  /** Route param value. The literal id "new" means create mode. */
  personaId: string;
}

interface DraftState {
  name: string;
  role: string;
  model: string;
  systemPrompt: string;
  tools: string[];
}

const EMPTY_DRAFT: DraftState = {
  name: "",
  role: "",
  model: MODEL_OPTIONS[0],
  systemPrompt: "",
  tools: [],
};

function draftFromPersona(p: Persona): DraftState {
  return {
    name: p.name,
    role: p.role,
    model: p.model,
    systemPrompt: p.bio ?? "",
    tools: p.tools ?? [],
  };
}

/**
 * Create/edit form, porting `personaMgmtScreen()`'s edit view from the
 * prototype (name, role, model dropdown, standing-instructions textarea,
 * tool/MCP permission checkboxes, save button), rendered via the shared
 * `Modal` (wide, matching the prototype's `{wide:true}` for this screen).
 *
 * THE BUG THIS FIXES: the prototype's first version kept the "currently
 * editing" persona in one long-lived `state.editingPersona` variable at
 * module scope. Closing the form only sometimes reset it (whichever
 * `data-nav` handler happened to run), so reopening the persona list could
 * still show the stale edit form underneath. The fix here has two parts:
 *
 * 1. All form state (`draft`, `loading`, `saving`, `existing`) is local
 *    `useState` inside THIS component, never lifted into a context, store,
 *    or module-level variable that could outlive navigation away from this
 *    route -- so React unmounting this component (which happens whenever
 *    `router.push("/personas")` navigates away) is what clears it, not an
 *    explicit reset call that's easy to forget on one exit path.
 * 2. The parent page (`page.tsx`) renders this component with
 *    `key={personaId}`. Next's App Router reuses the same component
 *    instance across a dynamic-segment change within one route (navigating
 *    /personas/ada -> /personas/rex does NOT remount by default). `draft`
 *    and `existing` survive that fine either way -- the fetch effect below
 *    depends on `personaId` and overwrites both on every id change. But
 *    `saveError` doesn't: it's set by `handleSave` and nothing resets it
 *    when the id changes, so without the key, a failed save on one persona
 *    would still be showing under the next persona's freshly-loaded form.
 *    The `key` forces a fresh mount -- and fresh state for everything,
 *    including whatever a future edit adds here -- whenever the id changes,
 *    rather than depending on this effect being kept in sync with every
 *    field by hand. See PersonaEditForm.test.tsx, in particular "does not
 *    carry a failed save's error onto a different persona", which is the
 *    one that actually regresses if the key is removed.
 */
export function PersonaEditForm({ personaId }: PersonaEditFormProps) {
  const router = useRouter();
  const isNew = personaId === "new";

  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [existing, setExisting] = useState<Persona | null>(null);

  useEffect(() => {
    if (isNew) {
      setDraft(EMPTY_DRAFT);
      setExisting(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    // getPersonaById() (lib/safeApi.ts) fetches the roster and finds the
    // match, falling back to lib/mockData.ts's fixtures when the backend
    // isn't reachable -- lib/api.ts itself exposes no getPersona(id).
    getPersonaById(personaId).then((found) => {
      if (cancelled) return;
      setExisting(found);
      setDraft(found ? draftFromPersona(found) : EMPTY_DRAFT);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [personaId, isNew]);

  function closeToList() {
    router.push("/personas");
  }

  function toggleTool(tool: string) {
    setDraft((d) => ({
      ...d,
      tools: d.tools.includes(tool) ? d.tools.filter((t) => t !== tool) : [...d.tools, tool],
    }));
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      if (isNew) {
        await createPersona({
          name: draft.name,
          role: draft.role,
          model: draft.model,
          status: "offline",
          color: NEW_PERSONA_COLOR,
          systemPrompt: draft.systemPrompt,
          tools: draft.tools,
        });
      } else if (existing) {
        await updatePersona(existing.id, {
          name: draft.name,
          role: draft.role,
          model: draft.model,
          systemPrompt: draft.systemPrompt,
          tools: draft.tools,
        });
      }
      // Only navigate away on success -- closeToList() used to live in a
      // `finally` block, which also ran on a rejected save. That silently
      // discarded the user's edits and returned them to the list looking
      // like it had saved, while the rejection itself became an unhandled
      // promise rejection (this onClick handler is fire-and-forget from
      // React's perspective). createPersona/updatePersona don't exist in
      // lib/api.ts yet and there's no backend in this environment, so this
      // isn't a hypothetical: today, the first real Save click hits exactly
      // this path.
      closeToList();
    } catch {
      setSaving(false);
      setSaveError("Couldn't save — try again.");
    }
  }

  // The original persona's name, not the live-edited draft -- so the modal
  // title doesn't jump around as the user types, matching the prototype
  // (its title used the persona object directly, not any live form state).
  const title = isNew ? "New persona" : existing ? `Edit ${existing.name}` : "Edit persona";

  return (
    <Modal title={title} onClose={closeToList} wide>
      {loading ? (
        <div className="empty-hint">Loading…</div>
      ) : (
        <>
          <div className="form-row">
            <label className="field-label" htmlFor="persona-name">
              Name
            </label>
            <input
              id="persona-name"
              className="input"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            />
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-role">
              Role
            </label>
            <input
              id="persona-role"
              className="input"
              value={draft.role}
              onChange={(e) => setDraft((d) => ({ ...d, role: e.target.value }))}
            />
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-model">
              Model
            </label>
            <select
              id="persona-model"
              className="input"
              value={draft.model}
              onChange={(e) => setDraft((d) => ({ ...d, model: e.target.value }))}
            >
              {MODEL_OPTIONS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-system-prompt">
              Standing instructions
            </label>
            <textarea
              id="persona-system-prompt"
              className="textarea"
              value={draft.systemPrompt}
              onChange={(e) => setDraft((d) => ({ ...d, systemPrompt: e.target.value }))}
            />
          </div>

          <div className="form-row">
            <label className="field-label">Tools &amp; MCP servers</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {TOOL_OPTIONS.map((tool) => (
                <label
                  key={tool}
                  className={`chip ${draft.tools.includes(tool) ? "on" : ""}`}
                  style={{ position: "relative", cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={draft.tools.includes(tool)}
                    onChange={() => toggleTool(tool)}
                    style={{
                      position: "absolute",
                      width: 1,
                      height: 1,
                      padding: 0,
                      margin: -1,
                      overflow: "hidden",
                      clip: "rect(0,0,0,0)",
                      whiteSpace: "nowrap",
                      border: 0,
                    }}
                  />
                  {tool}
                </label>
              ))}
            </div>
          </div>

          {saveError && (
            <div className="empty-hint" style={{ color: "var(--danger)", marginBottom: 8 }}>
              {saveError}
            </div>
          )}
          <button type="button" className="btn btn-primary btn-block" onClick={handleSave} disabled={saving}>
            <CheckIcon size={13} /> {saving ? "Saving…" : "Save persona"}
          </button>
        </>
      )}
    </Modal>
  );
}
