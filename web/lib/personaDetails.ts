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

import type { Persona } from "@/lib/api";

export const STATUS_LABELS: Record<Persona["status"], string> = {
  online: "Online",
  busy: "Working",
  paused: "Paused",
  offline: "Offline",
};

/**
 * Model choices for the persona edit form's dropdown. Neither `lib/api.ts`
 * nor `lib/safeApi.ts` expose a "list available models" endpoint, and the
 * prototype only ever rendered the persona's current model as a single
 * fixed `<option>`. This list is a judgment call, assembled from the five
 * providers in ModelProvidersPanel plus the four models actually used by
 * the prototype's seed personas (Ada/Rex/Vex/Nova) -- kept as one exported
 * const so it's a single edit once real per-provider model listings exist.
 */
export const MODEL_OPTIONS = [
  "Claude Opus 4.8",
  "Claude Sonnet 5",
  "DeepSeek V3.2",
  "Gemini 3 Pro",
  "Qwen 3 Max",
  "OpenRouter (custom)",
];

/**
 * Tool / MCP permission checkboxes for the persona edit form.
 * `updatePersona`'s patch accepts `tools?: string[]` but the shared contract
 * defines no canonical tool taxonomy. Assembled from the four metamcp
 * servers surfaced in ToolsAndMcpPanel (filesystem/git/terminal/browser)
 * plus the tool strings that actually appear across the prototype's seed
 * personas. Judgment call -- single place to edit once real permission data
 * exists.
 */
export const TOOL_OPTIONS = [
  "File read",
  "File edit",
  "Read-only shell",
  "Shell exec",
  "Git",
  "Test runner",
  "Deploy pipeline",
  "MCP: filesystem",
  "MCP: git",
  "MCP: terminal",
  "MCP: browser",
  "MCP: cloud-deploy",
];

/** Neutral default for a freshly-created persona that hasn't picked a color yet. */
export const NEW_PERSONA_COLOR = "#64748B";
