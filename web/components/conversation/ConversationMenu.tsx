"use client";

// The conversation topbar's 3-dot dropdown: Settings / Archive-Unarchive /
// Delete. Same absolutely-positioned-popover pattern ModelSwitcher's own
// .scope-picker uses (see that component's comment) — no dedicated
// dropdown primitive exists in components/ui/ yet.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { deleteConversation, setConversationArchived } from "@/lib/api";
import { ArchiveIcon, DotsIcon, SettingsIcon, TrashIcon } from "@/components/ui/icons";

export interface ConversationMenuProps {
  conversationId: string;
  archived: boolean;
  onArchivedChanged: (archived: boolean) => void;
  onOpenSettings: () => void;
}

export function ConversationMenu({
  conversationId,
  archived,
  onArchivedChanged,
  onOpenSettings,
}: ConversationMenuProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(event: MouseEvent) {
      if (anchorRef.current && !anchorRef.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  async function toggleArchived() {
    if (busy) return;
    setBusy(true);
    try {
      await setConversationArchived(conversationId, !archived);
      onArchivedChanged(!archived);
      setOpen(false);
      // RosterList is a SEPARATE server-fetched component (app/conversation/
      // [id]/page.tsx) -- this only updates ConversationView's own local
      // `archived` state, which the sidebar has no way to see. Found live:
      // without this, the sidebar only ever picked up the change on the
      // NEXT full navigation (Next.js re-running the server component then),
      // not immediately. router.refresh() re-runs that server fetch in
      // place so both panes agree right away.
      router.refresh();
    } catch (error) {
      console.error("Failed to change archive state", error);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (busy) return;
    if (!window.confirm("Delete this conversation? This hides it from your conversation list.")) return;
    setBusy(true);
    try {
      await deleteConversation(conversationId);
      router.push("/");
      router.refresh();
    } catch (error) {
      console.error("Failed to delete conversation", error);
      setBusy(false);
    }
  }

  return (
    <div className="conv-menu-anchor" ref={anchorRef}>
      <button
        type="button"
        className="icon-btn"
        aria-label="Conversation options"
        onClick={() => setOpen((value) => !value)}
      >
        <DotsIcon size={18} />
      </button>
      {open && (
        <div className="conv-menu">
          <button
            type="button"
            className="conv-menu-item"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
          >
            <SettingsIcon size={15} /> Settings
          </button>
          <button type="button" className="conv-menu-item" disabled={busy} onClick={() => void toggleArchived()}>
            <ArchiveIcon size={15} /> {archived ? "Unarchive" : "Archive"}
          </button>
          <button type="button" className="conv-menu-item danger" disabled={busy} onClick={() => void handleDelete()}>
            <TrashIcon size={15} /> Delete
          </button>
        </div>
      )}
    </div>
  );
}
