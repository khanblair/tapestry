"use client";

import { useEffect, useState } from "react";
import { formatRelativeTime } from "@/lib/time";

const REFRESH_INTERVAL_MS = 30_000;

/**
 * Renders `formatRelativeTime(iso)`, without the server/client hydration
 * mismatch a direct call would produce: `formatRelativeTime`'s implicit
 * `now = new Date()` is evaluated at two genuinely different wall-clock
 * moments (once during server render, again during client hydration), so
 * the two renders can legitimately disagree (e.g. "4s" vs "5s") even
 * though nothing is actually wrong.
 *
 * Renders an empty placeholder on the server AND on the client's initial
 * hydration pass (both skip the `Date.now()`-dependent computation
 * entirely, so there's nothing to mismatch), then fills in the real,
 * live-updating value from a `useEffect` — which runs strictly AFTER
 * hydration completes, so it can never itself trigger a mismatch. Refreshed
 * every 30s so "2m ago" doesn't sit frozen for the life of the page.
 */
export function RelativeTime({ iso }: { iso: string }) {
  const [label, setLabel] = useState("");

  useEffect(() => {
    setLabel(formatRelativeTime(iso));
    const interval = setInterval(() => setLabel(formatRelativeTime(iso)), REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [iso]);

  return <>{label}</>;
}
