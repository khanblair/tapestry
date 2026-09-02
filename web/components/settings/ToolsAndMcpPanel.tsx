"use client";

import { useState } from "react";
import { Toggle } from "@/components/ui/Toggle";
import type { McpServerStatus, SystemStatus } from "@/lib/api";

export interface ToolsAndMcpPanelProps {
  /** `null` while GET /api/status is still loading (see SettingsTabs.tsx, which fetches it once and passes it down to all three status panels). */
  status: SystemStatus | null;
}

/**
 * metamcp's real live running state + tool/server list, from
 * `status.metamcp` / `status.mcpServers` (GET /api/status) -- previously a
 * hardcoded `MCP_SERVERS` array here, ported verbatim from the prototype's
 * `settingsScreen()` `tools` tab body.
 */
export function ToolsAndMcpPanel({ status }: ToolsAndMcpPanelProps) {
  if (status === null) {
    return <div className="empty-hint">Loading…</div>;
  }
  return <ToolsAndMcpList metamcp={status.metamcp} mcpServers={status.mcpServers} />;
}

// Split out so useState's initializer only ever runs once real data is
// available -- mounted fresh each time `status` first becomes non-null,
// since ToolsAndMcpPanel above doesn't render this until then.
function ToolsAndMcpList({
  metamcp,
  mcpServers,
}: {
  metamcp: SystemStatus["metamcp"];
  mcpServers: McpServerStatus[];
}) {
  const [enabled, setEnabled] = useState<boolean[]>(mcpServers.map((s) => s.connected));

  return (
    <div>
      <div className="toggle-row">
        <div>
          <div className="tt">metamcp</div>
          {/* metamcp.serverCount, not mcpServers.length -- separate fields on
              SystemStatus that can differ (metamcp's own count vs. the list
              of individual server rows below). */}
          <div className="td">Aggregator &middot; {metamcp.serverCount} servers connected</div>
        </div>
        <span className={`chip ${metamcp.running ? "on" : ""}`}>{metamcp.running ? "Running" : "Stopped"}</span>
      </div>
      {mcpServers.map((server, i) => (
        <div className="toggle-row" key={server.name}>
          <div className="tt mono" style={{ fontWeight: 500 }}>
            {server.name}
          </div>
          <Toggle
            checked={enabled[i]}
            label={`${server.name} MCP server`}
            onChange={(next) => setEnabled((prev) => prev.map((v, idx) => (idx === i ? next : v)))}
          />
        </div>
      ))}
    </div>
  );
}
