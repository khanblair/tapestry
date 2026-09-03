"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Conversation, Persona } from "@/lib/api";
import { RosterRow } from "./RosterRow";
import { YouAvatar } from "@/components/persona/PersonaAvatar";
import {
  ArchiveIcon,
  BellIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
} from "@/components/ui/icons";

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

const COLLAPSED_KEY = "tapestry:sidebarCollapsed";
const WIDTH_KEY = "tapestry:sidebarWidth";
const DEFAULT_WIDTH = 300;
const MIN_WIDTH = 220;
const MAX_WIDTH = 420;

/**
 * The roster screen's list pane: Direct Messages + Groups sections, a
 * local search filter, and the top bar (search/bell/new-conversation).
 * Rendered by both app/(roster)/page.tsx and app/conversation/[id]/
 * page.tsx so the two-pane desktop layout has the roster on both routes;
 * which pane is visible on mobile is a CSS/data-route concern (see
 * app/globals.css), not something this component decides.
 *
 * Collapse/resize state lives here (not a shared layout wrapper) and
 * persists to localStorage -- both routes that render this component pick
 * up the same width/collapsed state on navigation without any server-side
 * plumbing, at the cost of a one-frame flash of the default on first
 * paint (acceptable for a per-viewer UI preference, same tradeoff
 * localStorage-backed state always has).
 */
export function RosterList({ personas, conversations, activeConversationId, pendingApprovalCount = 0 }: RosterListProps) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [archivedOpen, setArchivedOpen] = useState(false);
  const resizing = useRef(false);

  useEffect(() => {
    try {
      const storedCollapsed = window.localStorage.getItem(COLLAPSED_KEY);
      if (storedCollapsed) setCollapsed(storedCollapsed === "true");
      const storedWidth = Number(window.localStorage.getItem(WIDTH_KEY));
      if (storedWidth >= MIN_WIDTH && storedWidth <= MAX_WIDTH) setWidth(storedWidth);
    } catch {
      // Private browsing / storage disabled -- fall back to defaults.
    }
  }, []);

  function setCollapsedPersisted(next: boolean) {
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSED_KEY, String(next));
    } catch {
      // Best-effort only.
    }
  }

  function startResize(event: React.PointerEvent) {
    event.preventDefault();
    resizing.current = true;
    const startX = event.clientX;
    const startWidth = width;

    function handleMove(moveEvent: PointerEvent) {
      if (!resizing.current) return;
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + (moveEvent.clientX - startX)));
      setWidth(next);
    }
    function handleUp() {
      resizing.current = false;
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      setWidth((current) => {
        try {
          window.localStorage.setItem(WIDTH_KEY, String(current));
        } catch {
          // Best-effort only.
        }
        return current;
      });
    }
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }

  const personaById = useMemo(() => {
    const map = new Map<string, Persona>();
    for (const p of personas) map.set(p.id, p);
    return map;
  }, [personas]);

  const { dms, groups, archived } = useMemo(() => {
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
      dms: filtered.filter((c) => c.kind === "dm" && !c.archived),
      groups: filtered.filter((c) => c.kind === "group" && !c.archived),
      archived: filtered.filter((c) => c.archived),
    };
  }, [conversations, personaById, query]);

  return (
    <>
      {/* Always rendered (never conditionally unmounted) -- collapsing is a
          desktop-only (900px+) visual effect purely via the .collapsed CSS
          class (see globals.css), so a `collapsed=true` value persisted
          from a wide screen can never blank out the roster or fight with
          the EXISTING, separate mobile/tablet show/hide rules
          ([data-route="conversation"] .pane-roster, flex:1 1 100% below
          768px, etc.) the way an early `return <button/>` here used to --
          found live: at a narrow width, that early return rendered NO
          roster panel at all, and the always-fixed-position expand button
          landed directly on top of the conversation topbar's own
          .conv-back-btn (both draw near the same top-left corner). Both
          the collapse toggle and the floating expand button are
          themselves CSS-hidden below 900px for the same reason. */}
      <button
        type="button"
        className={`icon-btn sidebar-expand-btn${collapsed ? " visible" : ""}`}
        aria-label="Show conversation list"
        onClick={() => setCollapsedPersisted(false)}
      >
        <PanelLeftOpenIcon size={18} />
      </button>
      <div
        className={`pane pane-roster${collapsed ? " collapsed" : ""}`}
        style={{ "--sidebar-width": `${width}px` } as React.CSSProperties}
      >
      <div className="topbar">
        <h2>Tapestry</h2>
        <span className="spacer" />
        <button
          type="button"
          className="icon-btn sidebar-collapse-btn"
          aria-label="Collapse conversation list"
          onClick={() => setCollapsedPersisted(true)}
        >
          <PanelLeftCloseIcon size={17} />
        </button>
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
        {archived.length > 0 && (
          <>
            <div
              className="roster-section-label"
              style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}
              onClick={() => setArchivedOpen((open) => !open)}
            >
              {archivedOpen ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
              <ArchiveIcon size={12} /> Archived ({archived.length})
            </div>
            {archivedOpen &&
              archived.map((c) => (
                <RosterRow key={c.id} conversation={c} personas={personas} active={c.id === activeConversationId} />
              ))}
          </>
        )}

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

      <div className="sidebar-resize-handle" onPointerDown={startResize} />
      </div>
    </>
  );
}
