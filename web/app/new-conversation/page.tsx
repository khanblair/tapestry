"use client";

// Screen 4: New DM / New Group tabs, persona picker with multi-select toggle
// for group creation. Ports the prototype's newConvoScreen() as a Modal
// screen (desktop centered / mobile full-cover), same pattern as
// app/profile/[personaId]/PersonaProfileView.tsx.
//
// Both tabs now call the real POST /api/conversations (lib/api.ts's
// createConversation) rather than demo-navigating to a fixed id: the DM
// tab's own links already relied on lazy-vivification (see api.py judgment
// call 5) and still do for the zero-click case, but "Create group" has no
// such fallback -- a group only exists once this call succeeds.

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Modal } from "@/components/ui/Modal";
import { PersonaAvatar } from "@/components/persona/PersonaAvatar";
import { PlusIcon } from "@/components/ui/icons";
import { safeGetPersonas } from "@/lib/safeApi";
import { createConversation, type Persona } from "@/lib/api";
import { useEffect } from "react";

type Tab = "dm" | "group";

export default function NewConversationPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("dm");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [groupName, setGroupName] = useState("#new-project");
  const [groupContext, setGroupContext] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    safeGetPersonas().then(setPersonas);
  }, []);

  function togglePick(id: string) {
    setPicked((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));
  }

  async function handleCreateGroup() {
    setCreating(true);
    setError(null);
    try {
      const conversation = await createConversation({
        kind: "group",
        name: groupName.trim() || undefined,
        personaIds: picked,
        context: groupContext.trim() || undefined,
      });
      router.push(`/conversation/${conversation.id}`);
    } catch {
      setError("Couldn't create the group. Try again.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <Modal title="New conversation" onClose={() => router.push("/")}>
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        <button type="button" className={`btn btn-sm${tab === "dm" ? " btn-primary" : ""}`} onClick={() => setTab("dm")}>
          New DM
        </button>
        <button type="button" className={`btn btn-sm${tab === "group" ? " btn-primary" : ""}`} onClick={() => setTab("group")}>
          New Group
        </button>
      </div>

      {tab === "dm" &&
        personas.map((p) => (
          <Link key={p.id} href={`/conversation/dm-${p.id}`} className="list-item">
            <PersonaAvatar persona={p} size="sm" />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{p.name}</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{p.role}</div>
            </div>
          </Link>
        ))}

      {tab === "group" && (
        <>
          <div className="form-row">
            <label className="field-label" htmlFor="group-name">
              Group name
            </label>
            <input id="group-name" className="input" value={groupName} onChange={(e) => setGroupName(e.target.value)} />
          </div>
          <div className="form-row">
            <label className="field-label" htmlFor="group-context">
              Ground rules / context (optional)
            </label>
            <textarea
              id="group-context"
              className="textarea"
              rows={3}
              placeholder="e.g. Casual hangout only, no work talk. Keep replies short."
              value={groupContext}
              onChange={(e) => setGroupContext(e.target.value)}
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              Shown to every member above their own instructions — takes precedence if they conflict.
            </div>
          </div>
          <label className="field-label">Members</label>
          {personas.map((p) => {
            const isPicked = picked.includes(p.id);
            return (
              <div
                key={p.id}
                className="list-item"
                role="button"
                tabIndex={0}
                aria-pressed={isPicked}
                onClick={() => togglePick(p.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    togglePick(p.id);
                  }
                }}
              >
                <PersonaAvatar persona={p} size="sm" />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{p.name}</div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{p.role}</div>
                </div>
                <span className={`chip${isPicked ? " on" : ""}`}>{isPicked ? "Added" : "Add"}</span>
              </div>
            );
          })}
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: 16 }}
            disabled={picked.length === 0 || creating}
            onClick={handleCreateGroup}
          >
            <PlusIcon size={13} /> {creating ? "Creating…" : "Create group"}
          </button>
          {error && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--danger, #dc2626)" }}>{error}</div>
          )}
        </>
      )}
    </Modal>
  );
}
