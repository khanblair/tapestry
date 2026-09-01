import { defineConfig, devices } from "@playwright/test";

// One project per breakpoint — mirrors the three device sizes validated in the prototype
// (390 / 834 / fluid-to-1180), since the responsive behavior genuinely restructures per
// breakpoint rather than just scaling.
export default defineConfig({
  testDir: "./tests/e2e",
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
  },
  use: {
    baseURL: "http://localhost:3000",
  },
  projects: [
    { name: "mobile", use: { ...devices["iPhone 13"], viewport: { width: 390, height: 844 } } },
    { name: "tablet", use: { viewport: { width: 834, height: 1112 } } },
    { name: "desktop", use: { viewport: { width: 1180, height: 800 } } },
  ],
});
