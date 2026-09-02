"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createPersona, updatePersona, type Mode, type Persona } from "@/lib/api";
import { getPersonaById, safeGetStatus } from "@/lib/safeApi";
import { MODE_OPTIONS, MODEL_OPTIONS, TOOL_OPTIONS, NEW_PERSONA_COLOR } from "@/lib/personaDetails";
import { CheckIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/Modal";

export interface PersonaEditFormProps {
  /** Route param value. The literal id "new" means create mode. */
  personaId: string;
}

// "None" sentinel for the optional `guardianModel` <select> — Persona's
// real field is `string | undefined`, but a controlled <select> needs a
// string value for every option, including the unset one.
const GUARDIAN_MODEL_NONE = "";

interface DraftState {
  name: string;
  role: string;
  model: string;
  systemPrompt: string;
  tools: string[];
  // --- tapestry_modes_models_personas_spec.md §3 / §3.2 — mirrors the six
  // new Persona/PersonaDraft fields one-for-one. `mcp` was already a real
  // field on Persona/PersonaDraft (lib/api.ts) but had no form field here at
  // all -- the exact gap PersonaEditForm.test.tsx's "does not silently drop
  // mcp" regression test below covers directly.
  mcp: string[];
  fallbackModels: string[];
  /** GUARDIAN_MODEL_NONE ("") means unset -- see that const's comment. */
  guardianModel: string;
  reasoningEffort: string;
  defaultMode: Mode;
  /** Kept as text so the field can be empty (unset -- use the global default) rather than coerced to 0. Parsed to a number (or omitted) in handleSave. */
  maxTurns: string;
  maxDelegationDepth: string;
}

const EMPTY_DRAFT: DraftState = {
  name: "",
  role: "",
  model: MODEL_OPTIONS[0].value,
  systemPrompt: "",
  tools: [],
  mcp: [],
  fallbackModels: [],
  guardianModel: GUARDIAN_MODEL_NONE,
  reasoningEffort: "",
  defaultMode: "manual",
  maxTurns: "",
  maxDelegationDepth: "",
};

function draftFromPersona(p: Persona): DraftState {
  return {
    name: p.name,
    role: p.role,
    model: p.model,
    systemPrompt: p.systemPrompt ?? "",
    tools: p.tools ?? [],
    mcp: p.mcp ?? [],
    fallbackModels: p.fallbackModels ?? [],
    guardianModel: p.guardianModel ?? GUARDIAN_MODEL_NONE,
    reasoningEffort: p.reasoningEffort ?? "",
    defaultMode: p.defaultMode ?? "manual",
    maxTurns: p.maxTurns != null ? String(p.maxTurns) : "",
    maxDelegationDepth: p.maxDelegationDepth != null ? String(p.maxDelegationDepth) : "",
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
  // The real, live metamcp server names -- same source ToolsAndMcpPanel.tsx
  // uses (GET /api/status via safeGetStatus(), falling back to
  // lib/mockData.ts's MOCK_STATUS when the backend isn't reachable), so the
  // `mcp` multi-select below offers exactly the servers that actually exist
  // rather than a second, hardcoded list that could drift from the real one.
  const [mcpOptions, setMcpOptions] = useState<string[]>([]);
  // Tracked separately from `mcpOptions.length === 0` -- an empty real
  // server list (metamcp down, nothing registered) is a legitimate loaded
  // state, not "still loading". Same shape as ToolsAndMcpPanel's own
  // `status === null` check.
  const [mcpLoaded, setMcpLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    safeGetStatus().then((status) => {
      if (cancelled) return;
      setMcpOptions(status.mcpServers.map((s) => s.name));
      setMcpLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

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

  // Same toggle pattern as toggleTool above, reused for `mcp` (the field
  // that used to be silently dropped) rather than a differently-shaped
  // multi-select control.
  function toggleMcp(server: string) {
    setDraft((d) => ({
      ...d,
      mcp: d.mcp.includes(server) ? d.mcp.filter((s) => s !== server) : [...d.mcp, server],
    }));
  }

  // A model can't meaningfully be its own fallback, so picking it as the
  // primary model drops it from `fallbackModels` too (see the `model`
  // <select>'s onChange below) -- toggling here only ever adds/removes a
  // model that isn't the current primary.
  function toggleFallbackModel(model: string) {
    setDraft((d) => ({
      ...d,
      fallbackModels: d.fallbackModels.includes(model)
        ? d.fallbackModels.filter((m) => m !== model)
        : [...d.fallbackModels, model],
    }));
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      // Shared by both createPersona and updatePersona below -- every field
      // in tapestry_modes_models_personas_spec.md §3 gets wired through
      // exactly the way the pre-existing four fields already were.
      // Empty-string/empty-list values are normalized to `undefined` for the
      // truly optional, nullable fields (guardianModel, reasoningEffort,
      // maxTurns, maxDelegationDepth) so an unset field round-trips as unset
      // rather than as an empty string or 0.
      const shared = {
        name: draft.name,
        role: draft.role,
        model: draft.model,
        systemPrompt: draft.systemPrompt,
        tools: draft.tools,
        mcp: draft.mcp,
        fallbackModels: draft.fallbackModels,
        guardianModel: draft.guardianModel === GUARDIAN_MODEL_NONE ? undefined : draft.guardianModel,
        reasoningEffort: draft.reasoningEffort.trim() === "" ? undefined : draft.reasoningEffort.trim(),
        defaultMode: draft.defaultMode,
        maxTurns: draft.maxTurns.trim() === "" ? undefined : Number(draft.maxTurns),
        maxDelegationDepth: draft.maxDelegationDepth.trim() === "" ? undefined : Number(draft.maxDelegationDepth),
      };
      if (isNew) {
        await createPersona({
          ...shared,
          status: "offline",
          color: NEW_PERSONA_COLOR,
        });
      } else if (existing) {
        await updatePersona(existing.id, shared);
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
              onChange={(e) => {
                const nextModel = e.target.value;
                // A model can't meaningfully be its own fallback -- drop it
                // from fallbackModels if it's picked as the new primary.
                setDraft((d) => ({
                  ...d,
                  model: nextModel,
                  fallbackModels: d.fallbackModels.filter((m) => m !== nextModel),
                }));
              }}
            >
              {MODEL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
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
            <label className="field-label">Tools</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {TOOL_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={`chip ${draft.tools.includes(opt.value) ? "on" : ""}`}
                  style={{ position: "relative", cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={draft.tools.includes(opt.value)}
                    onChange={() => toggleTool(opt.value)}
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
                  {opt.label}
                </label>
              ))}
            </div>
          </div>

          <div className="form-row">
            <label className="field-label">MCP servers</label>
            {!mcpLoaded ? (
              <div className="empty-hint">Loading…</div>
            ) : mcpOptions.length === 0 ? (
              <div className="empty-hint">No MCP servers available.</div>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {mcpOptions.map((server) => (
                  <label
                    key={server}
                    className={`chip ${draft.mcp.includes(server) ? "on" : ""}`}
                    style={{ position: "relative", cursor: "pointer" }}
                  >
                    <input
                      type="checkbox"
                      checked={draft.mcp.includes(server)}
                      onChange={() => toggleMcp(server)}
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
                    {server}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="form-row">
            <label className="field-label">Fallback models</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {/* A model can't meaningfully be its own fallback -- excludes
                  whichever model is currently selected as primary. */}
              {MODEL_OPTIONS.filter((opt) => opt.value !== draft.model).map((opt) => (
                <label
                  key={opt.value}
                  className={`chip ${draft.fallbackModels.includes(opt.value) ? "on" : ""}`}
                  style={{ position: "relative", cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={draft.fallbackModels.includes(opt.value)}
                    onChange={() => toggleFallbackModel(opt.value)}
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
                  {opt.label}
                </label>
              ))}
            </div>
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-guardian-model">
              Guardian model
            </label>
            <select
              id="persona-guardian-model"
              className="input"
              value={draft.guardianModel}
              onChange={(e) => setDraft((d) => ({ ...d, guardianModel: e.target.value }))}
            >
              <option value={GUARDIAN_MODEL_NONE}>None</option>
              {MODEL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-reasoning-effort">
              Reasoning effort
            </label>
            <input
              id="persona-reasoning-effort"
              className="input"
              placeholder="e.g. low, medium, high"
              value={draft.reasoningEffort}
              onChange={(e) => setDraft((d) => ({ ...d, reasoningEffort: e.target.value }))}
            />
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-default-mode">
              Default mode
            </label>
            <select
              id="persona-default-mode"
              className="input"
              value={draft.defaultMode}
              onChange={(e) => setDraft((d) => ({ ...d, defaultMode: e.target.value as Mode }))}
            >
              {MODE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value} title={opt.description}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-max-turns">
              Max turns
            </label>
            <input
              id="persona-max-turns"
              className="input"
              type="number"
              min={1}
              placeholder="Default (10)"
              value={draft.maxTurns}
              onChange={(e) => setDraft((d) => ({ ...d, maxTurns: e.target.value }))}
            />
          </div>

          <div className="form-row">
            <label className="field-label" htmlFor="persona-max-delegation-depth">
              Max delegation depth
            </label>
            <input
              id="persona-max-delegation-depth"
              className="input"
              type="number"
              min={1}
              placeholder="Default (3)"
              value={draft.maxDelegationDepth}
              onChange={(e) => setDraft((d) => ({ ...d, maxDelegationDepth: e.target.value }))}
            />
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
