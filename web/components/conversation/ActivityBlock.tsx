import { CheckIcon } from "@/components/ui/icons";

export interface ActivityBlockProps {
  label: string;
  done: boolean;
  result?: string;
}

/**
 * Collapsible-looking "running pytest tests/auth/" block under a
 * message, with a spinning indicator while in progress and a checkmark
 * plus result once done. Matches .activity-block in app/globals.css.
 *
 * Note: the prototype embedded raw HTML in `label` (e.g. `Running
 * <span class="amono">pytest ...</span>`) so part of the string could be
 * styled monospace. Since `label`/`result` here are backend-controlled
 * strings delivered over the wire (Message.activity), this renders them
 * as plain text rather than dangerouslySetInnerHTML — an intentional
 * divergence from the prototype's markup to avoid an XSS vector. The
 * whole label is styled monospace instead, which reads the same for the
 * "Running <command>" phrasing the prototype actually used.
 */
export function ActivityBlock({ label, done, result }: ActivityBlockProps) {
  return (
    <div className={`activity-block${done ? " done" : ""}`}>
      {done ? (
        <span className="activity-check">
          <CheckIcon size={14} />
        </span>
      ) : (
        <span className="activity-spin" data-testid="activity-spin" />
      )}
      <span className="amono">{label}</span>
      {done && result && (
        <>
          <span className="spacer" />
          <span className="amono" style={{ color: "var(--text-faint)" }}>
            {result}
          </span>
        </>
      )}
    </div>
  );
}
