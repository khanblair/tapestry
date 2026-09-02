// searchAll(query) — a real backend search endpoint doesn't exist yet, per
// the task brief. Kept as its own module (not lib/api.ts) so swapping in the
// real endpoint later is a one-file change. Tries `${API_URL}/api/search?q=`
// first and falls back to a client-side scan of the mock personas/messages
// (lib/mockData.ts, via lib/safeApi.ts) so the Search screen is demoable
// without a backend.

import type { Persona } from "./api";
import { safeGetConversations, safeGetMessages, safeGetPersonas } from "./safeApi";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export interface SearchMessageResult {
  kind: "message";
  conversationId: string;
  conversationLabel: string;
  actor: string;
  snippet: string;
}

export interface SearchPersonaResult {
  kind: "persona";
  persona: Persona;
}

export interface SearchResults {
  messages: SearchMessageResult[];
  personas: SearchPersonaResult[];
}

function highlightSnippet(text: string, query: string, radius = 40): string {
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text.length > 80 ? `${text.slice(0, 80)}…` : text;
  const start = Math.max(0, idx - radius);
  const end = Math.min(text.length, idx + query.length + radius);
  return `${start > 0 ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
}

async function localSearch(query: string): Promise<SearchResults> {
  const q = query.trim().toLowerCase();
  if (!q) return { messages: [], personas: [] };

  const [conversations, personas] = await Promise.all([safeGetConversations(), safeGetPersonas()]);

  const messages: SearchMessageResult[] = [];
  for (const convo of conversations) {
    const msgs = await safeGetMessages(convo.id);
    const label = convo.kind === "group" ? convo.name ?? convo.id : convo.id;
    for (const m of msgs) {
      if (m.text.toLowerCase().includes(q)) {
        messages.push({
          kind: "message",
          conversationId: convo.id,
          conversationLabel: label,
          actor: m.actor,
          snippet: highlightSnippet(m.text, q),
        });
      }
    }
  }

  const personaResults: SearchPersonaResult[] = personas
    .filter((p) => p.name.toLowerCase().includes(q) || p.role.toLowerCase().includes(q))
    .map((persona) => ({ kind: "persona" as const, persona }));

  return { messages, personas: personaResults };
}

export async function searchAll(query: string): Promise<SearchResults> {
  const q = query.trim();
  if (!q) return { messages: [], personas: [] };

  try {
    const res = await fetch(`${API_URL}/api/search?q=${encodeURIComponent(q)}`, {
      signal: AbortSignal.timeout(2000),
    });
    if (res.ok) {
      return (await res.json()) as SearchResults;
    }
  } catch {
    // fall through to local search
  }
  return localSearch(q);
}
