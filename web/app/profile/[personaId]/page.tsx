import { PersonaProfileView } from "./PersonaProfileView";

/**
 * Server wrapper unwrapping Next.js 15's async `params` (see the sibling
 * personas/[personaId]/page.tsx for the same pattern) before handing a plain
 * string down to the client view. This route is read-only, so there's no
 * form-state bug class to guard against here, but the id is still passed
 * down as a fresh prop per navigation.
 */
export default async function PersonaProfilePage({
  params,
}: {
  params: Promise<{ personaId: string }>;
}) {
  const { personaId } = await params;
  return <PersonaProfileView personaId={personaId} />;
}
