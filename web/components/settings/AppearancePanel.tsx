"use client";

import { useState } from "react";
import { useTheme } from "@/lib/theme";
import { Toggle } from "@/components/ui/Toggle";
import { Button } from "@/components/ui/Button";

/**
 * Ported from `settingsScreen()`'s `appearance` tab body in the prototype.
 * The prototype's own copy for this row read "Switches the whole app — same
 * control as the prototype toolbar", which described the *prototype's* dual
 * theme control (this settings screen vs. the meta-toolbar above the device
 * frame). That toolbar doesn't exist in the real app, so the sub-copy is
 * adapted to "Switches the whole app" -- an intentional deviation, not a
 * missed port.
 *
 * This is wired to the real, shared theme state via `useTheme()` from
 * `lib/theme.ts`, not a fake local prop: the whole point of this panel (per
 * the task brief) is that toggling it here changes the live app theme
 * everywhere, not just a control that happens to live on the settings page.
 *
 * `useTheme()`'s exact shape isn't specified in the shared contract handed to
 * this agent. Assumed here: `{ theme: "light" | "dark"; setTheme: (t) =>
 * void }` -- the natural counterpart to the CSS-token theme (`data-theme`)
 * already used everywhere. Flagged for reconciliation with whoever owns
 * `lib/theme.ts`; this is the only file in this batch that calls it.
 */
export function AppearancePanel() {
  const { theme, setTheme } = useTheme();
  const [compact, setCompact] = useState(false);

  return (
    <div>
      <div className="toggle-row">
        <div>
          <div className="tt">Theme</div>
          <div className="td">Switches the whole app</div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Button size="sm" variant={theme === "light" ? "primary" : "default"} onClick={() => setTheme("light")}>
            Light
          </Button>
          <Button size="sm" variant={theme === "dark" ? "primary" : "default"} onClick={() => setTheme("dark")}>
            Dark
          </Button>
        </div>
      </div>
      <div className="toggle-row">
        <div className="tt">Compact messages</div>
        <Toggle checked={compact} label="Compact messages" onChange={setCompact} />
      </div>
    </div>
  );
}
