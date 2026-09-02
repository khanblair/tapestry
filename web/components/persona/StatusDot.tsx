import type { Persona } from "@/lib/api";

export type PersonaStatus = Persona["status"];

export interface StatusDotProps {
  status: PersonaStatus;
  /** Optional visible label alongside the dot, matching .status-pill (e.g. topbar "Online · Claude Opus 4.8"). */
  label?: string;
}

const STATUS_LABEL: Record<PersonaStatus, string> = {
  online: "Online",
  busy: "Working",
  paused: "Paused",
  offline: "Offline",
};

/**
 * The four-state presence dot. Uses the API's own status strings
 * ("online"/"busy"/"paused"/"offline") as both the prop type and the CSS
 * class name — the prototype used shorthand "on"/"off" classes, which
 * this deliberately does NOT reintroduce, since it's the exact mismatch
 * this component's test exists to catch.
 */
export function StatusDot({ status }: StatusDotProps) {
  return <span className={`dot ${status}`} data-testid="status-dot" title={STATUS_LABEL[status]} />;
}

/** The inline "● Online · Claude Opus 4.8" pill used in the conversation topbar and persona profile. */
export function StatusPill({ status, label }: StatusDotProps) {
  return (
    <span className="status-pill">
      <span className="sw" style={{ background: `var(--status-${status})` }} />
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}

export { STATUS_LABEL };
