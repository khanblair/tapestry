import type { SystemStatus } from "@/lib/api";

export interface ModelProvidersPanelProps {
  /** `null` while GET /api/status is still loading (see SettingsTabs.tsx, which fetches it once and passes it down to all three status panels). */
  status: SystemStatus | null;
}

/**
 * Which LiteLLM providers have an API key configured, from
 * `status.providers` (GET /api/status) -- previously a hardcoded
 * `PROVIDERS` array here, ported verbatim from the prototype's
 * `settingsScreen()` `models` tab body.
 */
export function ModelProvidersPanel({ status }: ModelProvidersPanelProps) {
  if (status === null) {
    return <div className="empty-hint">Loading…</div>;
  }

  return (
    <div>
      {status.providers.map((provider) => (
        <div className="toggle-row" key={provider.name}>
          <div className="tt">{provider.name}</div>
          <span className={`chip ${provider.connected ? "on" : ""}`}>
            {provider.connected ? "Connected" : "Not connected"}
          </span>
        </div>
      ))}
      <div className="empty-hint" style={{ marginTop: 10 }}>
        Routed through LiteLLM — personas pick a provider individually in Persona Management.
      </div>
    </div>
  );
}
