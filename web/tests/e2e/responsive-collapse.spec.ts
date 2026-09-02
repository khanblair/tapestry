import { test, expect } from "@playwright/test";

// One spec, run once per breakpoint project (mobile/tablet/desktop — see
// playwright.config.ts), covering the two structural collapse behaviors the
// prototype validated by hand:
//
//   1. Roster <-> conversation single-pane collapse below 768px (app-shell
//      scope: app/(roster)/page.tsx, app/conversation/[id]/page.tsx,
//      app/globals.css's `[data-route]` rules).
//   2. Conversation + thread: a real third pane on desktop (>=900px)
//      alongside roster and conversation, vs. a full-cover overlay on
//      mobile/tablet (<900px) — this pass's own subsystem
//      (app/conversation/[id]/layout.tsx's `@thread` parallel-route slot).
//
// Runs against the seeded fixture data (lib/mockData.ts): the "grp-auth"
// (#auth-rework) group conversation and its thread "t1".

test.describe("roster / conversation single-pane collapse", () => {
  test("the roster route shows the roster pane", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Tapestry" })).toBeVisible();
  });

  test("opening a conversation shows the conversation pane, and the roster only alongside it above the 768px collapse", async ({
    page,
  }, testInfo) => {
    await page.goto("/conversation/grp-auth");

    await expect(page.getByRole("heading", { name: "#auth-rework" })).toBeVisible();

    const roster = page.getByRole("heading", { name: "Tapestry" });
    if (testInfo.project.name === "mobile") {
      // Below the 768px collapse, only one of {roster, conversation} is on
      // screen at a time — opening a conversation hides the roster pane.
      await expect(roster).toBeHidden();
    } else {
      // Tablet (834px) and desktop (1180px) keep both panes side by side.
      await expect(roster).toBeVisible();
    }
  });
});

test.describe("conversation + thread layout", () => {
  test("desktop renders the thread as a real third pane alongside roster and conversation", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "three-pane layout is desktop-only (>=900px)");

    await page.goto("/conversation/grp-auth/thread/t1");

    // All three panes present at once — this is the behavior a plain
    // full-page thread route (no @thread slot) could NOT produce.
    await expect(page.getByRole("heading", { name: "Tapestry" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "#auth-rework" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Thread" })).toBeVisible();

    const threadBox = await page.locator(".pane-thread").boundingBox();
    const viewport = page.viewportSize();
    expect(threadBox).not.toBeNull();
    // A static ~320px sidebar, not a full-viewport overlay.
    expect(threadBox!.width).toBeLessThan((viewport?.width ?? 0) * 0.5);
  });

  test("mobile/tablet render the thread as a full-cover overlay, not a sidebar", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "desktop", "full-cover overlay applies below the desktop breakpoint (<900px)");

    await page.goto("/conversation/grp-auth/thread/t1");

    await expect(page.getByRole("heading", { name: "Thread" })).toBeVisible();

    const threadBox = await page.locator(".pane-thread").boundingBox();
    const viewport = page.viewportSize();
    expect(threadBox).not.toBeNull();
    expect(threadBox!.width).toBeGreaterThan((viewport?.width ?? 0) * 0.9);
  });

  test("closing the thread returns to the plain conversation view with no leftover thread pane", async ({ page }) => {
    // Visits the conversation first, THEN the thread — mirroring the real
    // product flow (a thread is always reached by forward navigation from
    // its parent conversation) — rather than a single page.goto() straight
    // to the thread URL. This matters here specifically: ThreadTopbar's
    // close button uses router.back(), which needs a real prior history
    // entry to return to; a cold direct load has none.
    await page.goto("/conversation/grp-auth");
    await page.goto("/conversation/grp-auth/thread/t1");
    await expect(page.getByRole("heading", { name: "Thread" })).toBeVisible();

    // Whichever of back/close is visible at this breakpoint (Modal-style
    // toggle — see ThreadTopbar.tsx). Anchored so it doesn't also match
    // ConversationView's own "Back to conversations" link (plural — a
    // different control entirely).
    await page.getByRole("button", { name: /^(Back to conversation|Close thread)$/ }).click();

    await expect(page).toHaveURL(/\/conversation\/grp-auth$/);
    await expect(page.getByRole("heading", { name: "Thread" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "#auth-rework" })).toBeVisible();
  });

  test("a conversation with no thread open never renders a thread pane", async ({ page }) => {
    await page.goto("/conversation/grp-auth");
    await expect(page.getByRole("heading", { name: "#auth-rework" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Thread" })).toHaveCount(0);
    await expect(page.locator(".pane-thread")).toHaveCount(0);
  });
});
