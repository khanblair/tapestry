import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // No @vitejs/plugin-react in this project (Next.js uses its own SWC
  // transform, which Vitest doesn't share) -- so without an explicit JSX
  // setting here, esbuild's default "transform" mode emits
  // `React.createElement(...)` calls and every .tsx test fails at runtime
  // with "React is not defined". `jsx: "automatic"` switches esbuild to the
  // React 19 automatic runtime (`react/jsx-runtime`, already a transitive
  // dependency of `react`), so no new package is needed. Discovered while
  // adding this batch's own component tests; likely affects every other
  // .tsx test in the project the same way.
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    // Registers @testing-library/jest-dom's matchers (toBeInTheDocument,
    // etc.) globally — was an empty array, so every .test.tsx using those
    // matchers failed with "toBeInTheDocument is not a function".
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
