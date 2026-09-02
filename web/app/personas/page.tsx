"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { Persona } from "@/lib/api";
import { safeGetPersonas } from "@/lib/safeApi";
import { PersonaCard } from "@/components/persona/PersonaCard";
import { Modal } from "@/components/ui/Modal";
import { PlusIcon } from "@/components/ui/icons";

/**
 * Screen 8: persona MANAGEMENT list (the admin surface, reached via
 * Settings). Distinct from `app/profile/[personaId]` (the read-only profile
 * opened from a name in a message). Matches `personaMgmtScreen()`'s list
 * view in the prototype -- `wide`, closing back to the roster, same as the
 * prototype's `{backTo:'roster', wide:true}`.
 */
export default function PersonasPage() {
  const router = useRouter();
  const [personas, setPersonas] = useState<Persona[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    // safeGetPersonas() falls back to lib/mockData.ts's fixtures when the
    // backend isn't reachable -- see lib/safeApi.ts.
    safeGetPersonas().then((data) => {
      if (!cancelled) setPersonas(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Modal title="Personas" onClose={() => router.push("/")} wide>
      {personas === null && <div className="empty-hint">Loading personas…</div>}
      {personas?.length === 0 && <div className="empty-hint">No personas configured yet.</div>}
      {personas?.map((p) => (
        <PersonaCard key={p.id} persona={p} />
      ))}

      <Link href="/personas/new" className="btn btn-primary btn-block" style={{ marginTop: 14 }}>
        <PlusIcon size={13} /> New persona
      </Link>
    </Modal>
  );
}
