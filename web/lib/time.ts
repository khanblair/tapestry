/**
 * Relative time formatting for roster previews ("2m", "4m", "1h") — the
 * prototype hardcoded these strings; real data carries an ISO timestamp
 * (Conversation.updatedAt, Message.timestamp) that needs formatting.
 * Shared here so both sibling screens (search results, activity feed)
 * can reuse it instead of re-deriving their own.
 */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const diffMs = now.getTime() - then;
  const diffSec = Math.round(diffMs / 1000);

  if (diffSec < 5) return "now";
  if (diffSec < 60) return `${diffSec}s`;

  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m`;

  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h`;

  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d`;

  const diffWeek = Math.round(diffDay / 7);
  return `${diffWeek}w`;
}

/** Formats a message timestamp as a clock time, e.g. "10:24". */
export function formatClockTime(iso: string): string {
  // Fixture/mock data (lib/mockData.ts, ported from the prototype) uses
  // plain "HH:MM" strings rather than full ISO datetimes — pass those
  // through as-is instead of failing to parse them as a Date.
  if (/^\d{1,2}:\d{2}$/.test(iso)) return iso;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
