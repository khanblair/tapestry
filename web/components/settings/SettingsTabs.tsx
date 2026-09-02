"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { safeGetPersonas } from "@/lib/safeApi";
import { Button } from "@/components/ui/Button";
import { UsersIcon, ChevronRightIcon } from "@/components/ui/icons";
import { PlatformsPanel } from "./PlatformsPanel";
import { ModelProvidersPanel } from "./ModelProvidersPanel";
import { ToolsAndMcpPanel } from "./ToolsAndMcpPanel";
import { AppearancePanel } from "./AppearancePanel";

const TABS = [
  { id: "platforms", label: "Platforms" },
  { id: "models", label: "Model providers" },
  { id: "tools", label: "Tools & MCP" },
  { id: "appearance", label: "Appearance" },
] as const;

type SettingsTabId = (typeof TABS)[number]["id"];

/**
 * The four-tab settings switcher, matching `settingsScreen()` in the
 * prototype exactly: the "Personas" quick-link row rendered once above the
 * tab strip (present on every tab, per the task brief -- NOT duplicated
 * into each panel), then the tab buttons, then the active panel.
 */
export function SettingsTabs() {
  const [activeTab, setActiveTab] = useState<SettingsTabId>("platforms");
  const [personaCount, setPersonaCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    // safeGetPersonas() falls back to lib/mockData.ts's fixtures when the
    // backend isn't reachable, so this count is never just a bare "…" in
    // dev/demo environments -- see lib/safeApi.ts.
    safeGetPersonas().then((personas) => {
      if (!cancelled) setPersonaCount(personas.length);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <Link href="/personas" className="list-item" style={{ marginBottom: 16 }}>
        <div className="avatar sm" style={{ background: "linear-gradient(135deg,#3B82F6,#F43F5E)" }}>
          <UsersIcon size={13} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Personas</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {personaCount ?? "…"} configured &middot; models, tools, permissions
          </div>
        </div>
        <ChevronRightIcon size={15} />
      </Link>

      <div style={{ display: "flex", gap: 6, marginBottom: 18, flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <Button
            key={t.id}
            size="sm"
            variant={activeTab === t.id ? "primary" : "default"}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </Button>
        ))}
      </div>

      {activeTab === "platforms" && <PlatformsPanel />}
      {activeTab === "models" && <ModelProvidersPanel />}
      {activeTab === "tools" && <ToolsAndMcpPanel />}
      {activeTab === "appearance" && <AppearancePanel />}
    </div>
  );
}
