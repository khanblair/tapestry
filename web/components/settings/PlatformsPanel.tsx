"use client";

import { useState } from "react";
import { Toggle } from "@/components/ui/Toggle";
import type { PlatformStatus, SystemStatus } from "@/lib/api";

export interface PlatformsPanelProps {
  /** `null` while GET /api/status is still loading (see SettingsTabs.tsx, which fetches it once and passes it down to all three status panels). */
  status: SystemStatus | null;
}

/**
 * Which chat surfaces have a bot token configured, from `status.platforms`
 * (GET /api/status) -- previously a hardcoded `PLATFORMS` array here, ported
 * verbatim from the prototype's `settingsScreen()` `platforms` tab body.
 */
export function PlatformsPanel({ status }: PlatformsPanelProps) {
  if (status === null) {
    return <div className="empty-hint">Loading…</div>;
  }
  return <PlatformsList platforms={status.platforms} />;
}

// Split out so useState's initializer only ever runs once real data is
// available -- mounted fresh each time `status` first becomes non-null,
// since PlatformsPanel above doesn't render this until then.
function PlatformsList({ platforms }: { platforms: PlatformStatus[] }) {
  const [connected, setConnected] = useState<boolean[]>(platforms.map((p) => p.connected));

  return (
    <div>
      {platforms.map((platform, i) => (
        <div className="toggle-row" key={platform.name}>
          <div>
            <div className="tt">{platform.name}</div>
            <div className="td">{platform.detail}</div>
          </div>
          {platform.alwaysOn ? (
            <span className="chip on">Active</span>
          ) : (
            <Toggle
              checked={connected[i]}
              label={`${platform.name} connection`}
              onChange={(next) =>
                setConnected((prev) => prev.map((v, idx) => (idx === i ? next : v)))
              }
            />
          )}
        </div>
      ))}
    </div>
  );
}
