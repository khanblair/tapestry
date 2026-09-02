"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { Persona } from "@/lib/api";
import { getPersonaById } from "@/lib/safeApi";
import { PersonaAvatar } from "@/components/persona/PersonaAvatar";
import { StatusDot } from "@/components/persona/StatusDot";
import { SendIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/Modal";
import { STATUS_LABELS } from "@/lib/personaDetails";

export interface PersonaProfileViewProps {
  personaId: string;
}

/**
 * Screen 5: the READ-ONLY persona profile, opened from a persona's name
 * elsewhere in the app (a message, search results, etc.). Ports
 * `profileScreen()` from the prototype exactly -- NOT `personaMgmtScreen()`,
 * which is the admin edit form at `app/personas/[personaId]`. Title is
 * literally "Persona" (not the persona's name), and section order is
 * Message button -> Model -> Standing instructions -> Permissions ->
 * Recent activity, matching the prototype. Not `wide`, matching the
 * prototype's `{backTo:'roster'}` (no `wide:true`) for this screen.
 */
export function PersonaProfileView({ personaId }: PersonaProfileViewProps) {
  const router = useRouter();
  const [persona, setPersona] = useState<Persona | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    // getPersonaById() (lib/safeApi.ts) fetches the roster and finds the
    // match, falling back to lib/mockData.ts's fixtures when the backend
    // isn't reachable -- lib/api.ts itself exposes no getPersona(id).
    getPersonaById(personaId).then((found) => {
      if (!cancelled) setPersona(found);
    });
    return () => {
      cancelled = true;
    };
  }, [personaId]);

  return (
    <Modal title="Persona" onClose={() => router.push("/")}>
      {persona === undefined && <div className="empty-hint">Loading…</div>}
      {persona === null && <div className="empty-hint">Persona not found.</div>}
      {persona && (
        <>
          <div style={{ display: "flex", gap: 14, alignItems: "center", marginBottom: 18 }}>
            <PersonaAvatar persona={persona} size="lg" />
            <div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{persona.name}</div>
              <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{persona.role}</div>
              <div className="status-pill" style={{ marginTop: 4 }}>
                <StatusDot status={persona.status} />
                {STATUS_LABELS[persona.status]}
              </div>
            </div>
          </div>

          <Link
            href={`/conversation/dm-${persona.id}`}
            className="btn btn-primary btn-block"
            style={{ marginBottom: 18 }}
          >
            <SendIcon size={13} /> Message {persona.name}
          </Link>

          <div className="section-title">Model</div>
          <div className="mono" style={{ fontSize: 13 }}>
            {persona.model}
          </div>

          <div className="section-title">Standing instructions</div>
          <div style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.55 }}>
            {persona.bio || <span className="empty-hint">No standing instructions set.</span>}
          </div>

          <div className="section-title">Permissions</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {persona.tools && persona.tools.length > 0 ? (
              persona.tools.map((tool) => (
                <span key={tool} className="chip on">
                  {tool}
                </span>
              ))
            ) : (
              <span className="empty-hint">No tool permissions granted.</span>
            )}
          </div>

          <div className="section-title">Recent activity</div>
          <div
            style={{
              fontSize: "12.5px",
              color: "var(--text-muted)",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            {/* Placeholder copy ported verbatim from the prototype -- no
                per-persona activity-feed endpoint exists yet (lib/api.ts /
                lib/safeApi.ts) to source this from. */}
            <div>
              Ran test suite on <span className="mono">feat/oauth-google</span> &middot; 4m ago
            </div>
            <div>Opened diff for review &middot; 12m ago</div>
          </div>
        </>
      )}
    </Modal>
  );
}
