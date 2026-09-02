import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// vitest.config.ts doesn't set test.globals, so React Testing Library's
// own auto-cleanup detection (which looks for a global `afterEach`) never
// fires — every render() from a previous test case stays mounted and
// leaks into the next one (most visible with it.each / multiple renders
// in one test file). Wiring it explicitly here fixes it for every test.
afterEach(() => {
  cleanup();
});
