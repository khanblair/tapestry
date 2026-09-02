"use client";

import { useEffect, useState } from "react";
import { formatClockTime } from "@/lib/time";

/**
 * Renders `formatClockTime(iso)` without the server/client hydration
 * mismatch a direct call produces: `toLocaleTimeString(undefined, ...)`
 * resolves to the RUNNING PROCESS's default timezone, and the Node.js
 * server and the browser almost never agree on one (e.g. a Docker
 * container defaulting to UTC vs. a browser in the viewer's local zone) --
 * so the server-rendered clock time and the client's first render can
 * legitimately disagree by whole hours. Same root problem, same fix, as
 * `components/roster/RelativeTime.tsx`.
 *
 * Renders an empty placeholder on the server AND on the client's initial
 * hydration pass, then fills in the real value from a `useEffect`, which
 * runs strictly after hydration completes and so can never itself cause a
 * mismatch.
 */
export function ClockTime({ iso }: { iso: string }) {
  const [label, setLabel] = useState("");

  useEffect(() => {
    setLabel(formatClockTime(iso));
  }, [iso]);

  return <>{label}</>;
}
