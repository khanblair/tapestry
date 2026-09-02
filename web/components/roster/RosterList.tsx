"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { Conversation, Persona } from "@/lib/api";
import { RosterRow } from "./RosterRow";
import { YouAvatar } from "@/components/persona/PersonaAvatar";
import { BellIcon, PlusIcon, SearchIcon, SettingsIcon } from "@/components/ui/icons";

export interface RosterListProps {
  personas: Persona[];
  conversations: Conversation[];
  activeConversationId?: string;
  /**
   * Count shown on the bell badge. Approvals/activity data lives in a
   * sibling's domain (components/approvals/, app/activity/) — this is a
   * plain number prop rather than RosterList reaching into that state
   * itself. Omit or pass 0 to hide the badge.
   */
  pendingApprovalCount?: number;
}

function byMostRecent(a: Conversation, b: Conversation): number {
  return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
}

/**
 * The roster screen's list pane: Direct Messages + Groups sections, a
 * local search filter, and the top bar (search/bell/new-conversation).
 * Rendered by both app/(roster)/page.tsx and app/conversation/[id]/
 * page.tsx so the two-pane desktop layout has the roster on both routes;
 * which pane is visible on mobile is a CSS/data-route concern (see
 * app/globals.css), not something this component decides.
 */
export function RosterList({ personas, conversations, activeConversationId, pendingApprovalCount = 0 }: RosterListProps) {
  const [query, setQuery] = useState("");

  const personaById = useMemo(() => {
    const map = new Map<string, Persona>();
    for (const p of personas) map.set(p.id, p);
    return map;
  }, [personas]);

  const { dms, groups } = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    function matches(c: Conversation): boolean {
      if (!normalized) return true;
      const persona = personaById.get(c.personaIds[0]);
      const name = c.kind === "group" ? c.name ?? "" : persona?.name ?? "";
      return (
        name.toLowerCase().includes(normalized) ||
        (c.lastPreview ?? "").toLowerCase().includes(normalized)
      );
    }
    const filtered = conversations.filter(matches).sort(byMostRecent);
    return {
      dms: filtered.filter((c) => c.kind === "dm"),
      groups: filtered.filter((c) => c.kind === "group"),
    };
  }, [conversations, personaById, query]);

  return (
    <div className="pane pane-roster">
      <div className="topbar">
        <h2>Tapestry</h2>
        <span className="spacer" />
        <Link href="/search" className="icon-btn" aria-label="Search">
          <SearchIcon size={18} />
        </Link>
        <Link href="/activity" className="icon-btn" aria-label="Activity" style={{ position: "relative" }}>
          <BellIcon size={18} />
          {pendingApprovalCount > 0 && (
            <span className="roster-badge" style={{ position: "absolute", top: 2, right: 2 }}>
              {pendingApprovalCount}
            </span>
          )}
        </Link>
        <Link href="/new-conversation" className="icon-btn" aria-label="New conversation">
          <PlusIcon size={18} />
        </Link>
      </div>

      <div className="roster-search">
        <input
          className="input"
          placeholder="Search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <SearchIcon size={15} />
      </div>

      <div className="scroll">
        <div className="roster-section-label">Direct Messages</div>
        {dms.length === 0 && <div className="empty-hint" style={{ padding: "0 14px 10px" }}>No direct messages yet.</div>}
        {dms.map((c) => (
          <RosterRow key={c.id} conversation={c} personas={personas} active={c.id === activeConversationId} />
        ))}

        <div className="roster-section-label">Groups</div>
        {groups.length === 0 && <div className="empty-hint" style={{ padding: "0 14px 10px" }}>No groups yet.</div>}
        {groups.map((c) => (
          <RosterRow key={c.id} conversation={c} personas={personas} active={c.id === activeConversationId} />
        ))}
      </div>

      <Link
        href="/settings"
        style={{
          padding: "10px 14px",
          borderTop: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 9,
          cursor: "pointer",
          textDecoration: "none",
          color: "inherit",
        }}
      >
        <YouAvatar size="sm" />
        <div style={{ fontSize: 12.5, fontWeight: 600 }}>You</div>
        <span className="spacer" />
        <SettingsIcon size={17} />
      </Link>
    </div>
  );
}
