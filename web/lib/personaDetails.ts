// Local supplement to the shared `Persona` contract in `lib/api.ts`.
//
// Earlier revision of this file defined its own `PersonaDetail` type
// extending `Persona` with bio/tools/mcp, plus a hand-copied seed-data
// dictionary -- written before `lib/api.ts` and `lib/mockData.ts` existed.
// Both have since landed: `Persona` already carries `bio?`, `tools?`, and
// `mcp?` as optional fields directly (see lib/api.ts), and `lib/mockData.ts`
// (via `lib/safeApi.ts`'s fallback) already supplies the same seed content
// for ada/rex/vex/nova. Duplicating that here would just be a second copy
// to drift out of sync, so this file now only holds what genuinely has no
// home elsewhere: display-label mapping and the edit form's option lists.

import type { Mode, Persona } from "@/lib/api";

export const STATUS_LABELS: Record<Persona["status"], string> = {
  online: "Online",
  busy: "Working",
  paused: "Paused",
  offline: "Offline",
};

/**
 * The five modes a conversation/persona can run under
 * (tapestry_modes_models_personas_spec.md §1) — one shared source for the
 * short label + longer description, used by both the conversation view's
 * mode switcher (components/conversation/ModeSwitcher.tsx) and
 * PersonaEditForm's `defaultMode` select, so the label strings exist in
 * exactly one place.
 */
export interface ModeOption {
  value: Mode;
  label: string;
  description: string;
}

export const MODE_OPTIONS: ModeOption[] = [
  { value: "manual", label: "Manual", description: "Always ask before mutating actions" },
  { value: "accept_edits", label: "Accept edits", description: "Auto-run file edits, still ask for shell/git/deploy" },
  { value: "auto", label: "Auto", description: "Uses judgment, screens risky actions before asking" },
  { value: "plan", label: "Plan", description: "Read-only this turn" },
  { value: "bypass", label: "Bypass", description: "Never asks — high trust" },
];

/**
 * Model choices for the persona edit form's dropdown and the conversation
 * view's model switcher. Neither `lib/api.ts` nor `lib/safeApi.ts` expose a
 * "list available models" endpoint, so this list is a judgment call — but
 * `value` must be the REAL string `litellm.acompletion(model=...)` expects,
 * not a display label, since it's sent to the backend verbatim
 * (createPersona/updatePersona's `model`, setConversationModel's `model`).
 *
 * Bug this fixes: an earlier revision was a flat `string[]` of display
 * labels ("Claude Sonnet 5", "DeepSeek V3.2", ...) used DIRECTLY as
 * `<option value>` — selecting one sent that label itself as the model
 * string, not a real id (confirmed live: POST .../model with "Claude
 * Sonnet 5" selected really did set the conversation's model to the
 * literal string "Claude Sonnet 5", which litellm.acompletion would 400 on
 * as an unrecognized model). `value` below is verified against real,
 * working persona configs (personas/{ada,rex,vex,nova}.yaml) plus
 * docs/vendor-research/ANALYSIS-litellm.md §3 for the one provider none of
 * the four seed personas use (Qwen — "dashscope/qwen-turbo" is that
 * doc's own verified-against-source example, not guessed).
 *
 * OpenRouter has no single correct value (its own model ids vary
 * per-model, e.g. "openrouter/anthropic/claude-sonnet-4" — see that same
 * ANALYSIS doc) — deliberately NOT included as a fixed option here rather
 * than pick an arbitrary one and imply it's the only choice. Selecting an
 * OpenRouter model needs a free-text affordance this dropdown doesn't
 * offer; out of scope for this fix.
 */
export interface ModelOption {
  value: string;
  label: string;
}

export const MODEL_OPTIONS: ModelOption[] = [
  { value: "claude-opus-4-6", label: "Claude Opus 4.6" },
  { value: "claude-sonnet-5", label: "Claude Sonnet 5" },
  { value: "deepseek/deepseek-chat", label: "DeepSeek Chat" },
  { value: "gemini/gemini-3-pro", label: "Gemini 3 Pro" },
  { value: "dashscope/qwen-turbo", label: "Qwen Turbo (DashScope)" },
];

/**
 * Tool permission checkboxes for the persona edit form. `value` is the
 * REAL `TOOL_REGISTRY` key (backend/tapestry/graph/build.py) — Persona.
 * tools is matched against that registry exactly (`tool_name not in
 * effective_tools` -> "not permitted", see persona_node) — not a display
 * label, following the same real-id-vs-label distinction MODEL_OPTIONS
 * above documents.
 *
 * Bug this fixes: an earlier revision was a flat `string[]` of display
 * labels ("File read", "MCP: filesystem", ...) used directly as the
 * checkbox's own value AND sent straight through to `tools: draft.tools`
 * on save. Since none of those strings match a real TOOL_REGISTRY key,
 * (a) a persona created through this form could never successfully use
 * ANY tool -- every real tool call would hit persona_node's "not
 * permitted" rejection -- and (b) editing an EXISTING persona (e.g. Rex,
 * real tools `["file_editor","terminal","git"]`) rendered every checkbox
 * unchecked (`draft.tools.includes("File edit")` never matches
 * `"file_editor"`), so saving without manually re-checking everything
 * would silently strip that persona's real tool permissions.
 *
 * The five "MCP: ..." entries are dropped entirely, not just fixed: they
 * never corresponded to a real backend concept (TOOL_REGISTRY has no
 * MCP-server-specific tool keys — MCP server access is `Persona.
 * mcp_servers`, a separate field with its own real, live-metamcp-backed
 * section on this form below). `skill_loader` has no checkbox here either
 * — see `_build_tool_schemas`: it's unconditionally available to every
 * persona regardless of `tools:`, not a permissioned capability.
 */
export interface ToolOption {
  value: string;
  label: string;
}

export const TOOL_OPTIONS: ToolOption[] = [
  { value: "file_editor_read", label: "File read" },
  { value: "file_editor", label: "File edit" },
  { value: "terminal_read_only", label: "Read-only shell" },
  { value: "terminal", label: "Shell exec" },
  { value: "git", label: "Git" },
  { value: "test_runner", label: "Test runner" },
  { value: "deploy_pipeline", label: "Deploy pipeline" },
];

/** Neutral default for a freshly-created persona that hasn't picked a color yet. */
export const NEW_PERSONA_COLOR = "#64748B";
