import { PersonaEditForm } from "./PersonaEditForm";

/**
 * Server wrapper for the create/edit route. Next.js 15 makes `params` a
 * Promise for App Router pages, so it's awaited here and handed down as a
 * plain string -- keeps the async-params plumbing out of the client
 * component that owns the actual form state and logic.
 *
 * `key={personaId}` is load-bearing: App Router reuses the same component
 * instance across a param change within one route, and PersonaEditForm's
 * data-fetch effect only resets the fields IT populates (draft, existing)
 * when personaId changes -- it doesn't know about every piece of local
 * state a future edit might add. Without the key, that non-refetched state
 * survives an id switch; today that's `saveError` (a failed save on Ada
 * would still read "Couldn't save" under Rex's freshly-loaded form). See
 * PersonaEditForm.tsx's doc comment and PersonaEditForm.test.tsx (the
 * "does not carry a failed save's error onto a different persona" case,
 * verified by removing this key and confirming that one test alone fails).
 */
export default async function PersonaEditPage({
  params,
}: {
  params: Promise<{ personaId: string }>;
}) {
  const { personaId } = await params;
  return <PersonaEditForm key={personaId} personaId={personaId} />;
}
