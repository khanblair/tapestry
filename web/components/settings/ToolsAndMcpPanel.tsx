"use client";

import { useState } from "react";
import { Toggle } from "@/components/ui/Toggle";

/**
 * Ported verbatim from `settingsScreen()`'s `tools` tab body in the
 * prototype: a metamcp status row plus one toggle row per connected server.
 */
const MCP_SERVERS = ["filesystem", "git", "terminal", "browser"] as const;

export function ToolsAndMcpPanel() {
  const [enabled, setEnabled] = useState<boolean[]>(MCP_SERVERS.map(() => true));

  return (
    <div>
      <div className="toggle-row">
        <div>
          <div className="tt">metamcp</div>
          <div className="td">Aggregator &middot; {MCP_SERVERS.length} servers connected</div>
        </div>
        <span className="chip on">Running</span>
      </div>
      {MCP_SERVERS.map((server, i) => (
        <div className="toggle-row" key={server}>
          <div className="tt mono" style={{ fontWeight: 500 }}>
            {server}
          </div>
          <Toggle
            checked={enabled[i]}
            label={`${server} MCP server`}
            onChange={(next) => setEnabled((prev) => prev.map((v, idx) => (idx === i ? next : v)))}
          />
        </div>
      ))}
    </div>
  );
}
