"use client";

// Screen 6: search input + grouped results (Messages, Personas). Ports the
// prototype's searchScreen() as a Modal screen (desktop centered / mobile
// full-cover), same pattern as app/profile/[personaId]/PersonaProfileView.tsx.
//
// A real search backend endpoint doesn't exist yet (per the task brief) —
// lib/search.ts's searchAll(query) tries `${API_URL}/api/search?q=` first and
// falls back to a client-side scan of lib/mockData.ts's fixtures via
// lib/safeApi.ts, so this screen is demoable/testable without a backend and
// the data source is a one-file swap once a real endpoint exists.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Modal } from "@/components/ui/Modal";
import { PersonaAvatar } from "@/components/persona/PersonaAvatar";
import { SearchIcon } from "@/components/ui/icons";
import { searchAll, type SearchResults } from "@/lib/search";
import { getPersonaMap } from "@/lib/safeApi";
import type { Persona } from "@/lib/api";

export default function SearchPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResults>({ messages: [], personas: [] });
  const [personaMap, setPersonaMap] = useState<Map<string, Persona>>(new Map());

  useEffect(() => {
    getPersonaMap().then(setPersonaMap);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(() => {
      searchAll(query).then((found) => {
        if (!cancelled) setResults(found);
      });
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query]);

  const hasQuery = query.trim().length > 0;
  const hasResults = results.messages.length > 0 || results.personas.length > 0;

  return (
    <Modal title="Search" onClose={() => router.push("/")}>
      <div className="input" style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
        <SearchIcon size={15} />
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search messages and personas"
          aria-label="Search"
          style={{ flex: 1, border: "none", background: "transparent", font: "inherit", color: "inherit", outline: "none" }}
        />
      </div>

      {!hasQuery && <div className="empty-hint">Start typing to search messages and personas.</div>}
      {hasQuery && !hasResults && <div className="empty-hint">No results for &ldquo;{query}&rdquo;.</div>}

      {results.messages.length > 0 && (
        <>
          <div className="section-title">Messages</div>
          {results.messages.map((m, i) => {
            const persona = m.actor === "you" ? null : personaMap.get(m.actor);
            return (
              <Link key={i} href={`/conversation/${m.conversationId}`} className="list-item">
                {persona ? (
                  <PersonaAvatar persona={persona} size="sm" />
                ) : (
                  <div className="avatar sm" style={{ background: "#475569" }}>
                    Y
                  </div>
                )}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {persona ? persona.name : "You"} in {m.conversationLabel}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{m.snippet}</div>
                </div>
              </Link>
            );
          })}
        </>
      )}

      {results.personas.length > 0 && (
        <>
          <div className="section-title">Personas</div>
          {results.personas.map(({ persona }) => (
            <Link key={persona.id} href={`/profile/${persona.id}`} className="list-item">
              <PersonaAvatar persona={persona} size="sm" />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{persona.name}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{persona.role}</div>
              </div>
            </Link>
          ))}
        </>
      )}
    </Modal>
  );
}
