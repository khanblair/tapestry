"use client";

import { useEffect, useState } from "react";
import { formatDayLabel } from "@/lib/time";

/**
 * Renders `formatDayLabel(iso)` without the server/client hydration
 * mismatch a direct call would produce — same root cause and same fix as
 * ClockTime.tsx / RelativeTime.tsx: "now" differs between the server
 * render and the client's first render, so "Today" could legitimately
 * flip to "Yesterday" (or vice versa) right around midnight.
 *
 * Renders nothing on the server and on the client's initial hydration
 * pass, then fills in the real label from a `useEffect`.
 */
export function DayChip({ iso }: { iso: string }) {
  const [label, setLabel] = useState("");

  useEffect(() => {
    setLabel(formatDayLabel(iso));
  }, [iso]);

  if (!label) return null;
  return <div className="day-chip">{label}</div>;
}
