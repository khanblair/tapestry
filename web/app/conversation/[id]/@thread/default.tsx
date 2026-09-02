/**
 * Fallback for the `@thread` parallel slot. Next.js needs this for every
 * URL under app/conversation/[id]/ that ISN'T a thread URL (the base
 * conversation, the diff screen, etc.) — without it, those routes 404 on a
 * hard reload because the `@thread` slot has no matching page to recover.
 *
 * Renders nothing: app/globals.css's `.thread-slot:empty { display: none }`
 * hides the wrapper entirely when this is what's mounted, so the
 * conversation pane gets the full width exactly as if there were no thread
 * slot at all.
 */
export default function ThreadDefault() {
  return null;
}
