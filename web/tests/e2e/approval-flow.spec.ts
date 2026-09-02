import { test, expect, type Page, type TestInfo } from "@playwright/test";

// Verifies the prototype-validated behavior the whole approvals system is
// built around: approving/rejecting in ANY of the three contexts (the inline
// card in a conversation, the diff screen's action bar, the Activity inbox)
// is reflected in the other two immediately, because all three read/write
// the same shared client-side store (lib/approvals.ts).
//
// Runs against the seeded fixture data (lib/mockData.ts): the "grp-auth"
// (#auth-rework) conversation has one pending approval, question id
// "appr-1" ("Merge feat/oauth-google → main"), linked to the diff at
// /conversation/grp-auth/diff/oauth-google.
//
// No backend is running anywhere this test suite executes (see
// tapestry_scoped_spec.md / project_structure.md — the web app is built
// against lib/api.ts's contract, backend implementation is separate work).
// lib/api.ts's answerAsk() will genuinely fail against a real fetch, and
// lib/approvals.ts's decideApproval() correctly reverts the optimistic
// update when that call fails (don't claim an approval succeeded if it
// didn't) — so every test here stubs the answer-endpoint response to
// simulate a working backend, which is the target/expected behavior once
// one exists.
//
// Uses in-app navigation (clicking links / page.goBack()) rather than
// page.goto() between assertions on purpose: lib/approvals.ts is a
// module-scoped store, not persisted storage — it only survives
// client-side route transitions within the same document, exactly like the
// prototype's in-memory `state.approvals`. A page.goto() is a real
// navigation (fresh document, fresh JS) and would reset it, which is correct
// behavior but not what these tests are checking.

async function mockApprovalEndpointSucceeds(page: Page) {
  await page.route("**/api/conversations/*/ask/answers", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
  );
}

/**
 * Clicks the roster's "Activity" link. Below the 768px roster/conversation
 * collapse, the roster pane (and its bell icon) is hidden while a
 * conversation is open — by design, the same single-pane behavior
 * responsive-collapse.spec.ts checks — so on mobile this first goes back to
 * the roster via the conversation topbar's "Back to conversations" button.
 * Both steps are client-side navigation, preserving the shared approvals
 * store.
 */
async function goToActivityFromConversation(page: Page, testInfo: TestInfo) {
  if (testInfo.project.name === "mobile") {
    await page.getByRole("link", { name: "Back to conversations" }).click();
  }
  await page.getByRole("link", { name: "Activity" }).click();
}

test("approving from the diff screen updates the inline card and the Activity inbox", async ({ page }, testInfo) => {
  await mockApprovalEndpointSucceeds(page);

  await page.goto("/conversation/grp-auth");
  await expect(page.getByText("Needs your approval")).toBeVisible();
  await expect(page.getByText("Merge feat/oauth-google → main")).toBeVisible();

  // Open the diff screen from the conversation header's "View diff" shortcut.
  await page.getByRole("link", { name: "View diff" }).click();
  await expect(page).toHaveURL(/\/conversation\/grp-auth\/diff\/oauth-google$/);
  await expect(page.getByRole("tab", { name: "oauth/google.py" })).toBeVisible();

  await page.getByRole("button", { name: "Approve merge" }).click();
  // ApprovalActions swaps its button pair for a status chip in place.
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve merge" })).toHaveCount(0);

  // Back to the conversation (client-side history nav — same store instance).
  await page.goBack();
  await expect(page).toHaveURL(/\/conversation\/grp-auth$/);
  await expect(page.getByText("Approved by you")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // The Activity inbox, reached via the roster's bell icon (also client-side
  // nav), no longer lists it as needing input.
  await goToActivityFromConversation(page, testInfo);
  await expect(page).toHaveURL(/\/activity$/);
  await expect(page.getByText("Nothing waiting on you right now.")).toBeVisible();
});

test("rejecting from the inline card is reflected in the Activity inbox", async ({ page }, testInfo) => {
  await mockApprovalEndpointSucceeds(page);

  await page.goto("/conversation/grp-auth");
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Changes requested")).toBeVisible();

  await goToActivityFromConversation(page, testInfo);
  await expect(page).toHaveURL(/\/activity$/);
  await expect(page.getByText("Nothing waiting on you right now.")).toBeVisible();
});

test("approving from the Activity inbox itself removes it from the list and updates the conversation", async ({ page }) => {
  await mockApprovalEndpointSucceeds(page);

  await page.goto("/activity");
  await expect(page.getByText("Merge feat/oauth-google → main")).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Nothing waiting on you right now.")).toBeVisible();

  // Back to the roster (Modal.tsx's back/close button — a router.push(), so
  // still client-side), then into the conversation via its roster row (also
  // client-side nav), so the shared store instance carries over rather than
  // being reset by a fresh page load.
  await page.getByRole("button", { name: /^(Back|Close)$/ }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("link", { name: /#auth-rework/ }).click();
  await expect(page).toHaveURL(/\/conversation\/grp-auth$/);
  await expect(page.getByText("Approved by you")).toBeVisible();
});
