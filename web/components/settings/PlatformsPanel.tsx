"use client";

import { useState } from "react";
import { Toggle } from "@/components/ui/Toggle";

/**
 * Ported verbatim from `settingsScreen()`'s `platforms` tab body in the
 * prototype: Discord connected, Telegram not connected, Web always-on (shown
 * as a static "Active" chip rather than a toggle, since the web surface
 * can't be turned off from here).
 */
const PLATFORMS = [
  { name: "Discord", detail: "Connected as @tapestry-bot", connected: true, alwaysOn: false },
  { name: "Telegram", detail: "Not connected", connected: false, alwaysOn: false },
  { name: "Web", detail: "Always on", connected: true, alwaysOn: true },
] as const;

export function PlatformsPanel() {
  const [connected, setConnected] = useState<boolean[]>(PLATFORMS.map((p) => p.connected));

  return (
    <div>
      {PLATFORMS.map((platform, i) => (
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
