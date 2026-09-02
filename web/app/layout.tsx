import type { Metadata } from "next";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tapestry",
  description: "A self-hosted, multi-agent workspace.",
};

// Runs before hydration to avoid a flash of the wrong theme. Deliberately
// stamps `data-theme` ONLY when localStorage holds an explicit choice —
// with no stored choice it stamps nothing at all, leaving the CSS
// `@media (prefers-color-scheme: dark)` tier (app/globals.css) to resolve
// the theme from the OS preference. useTheme() (lib/theme.ts) then
// initializes its React state from whatever's already on the page —
// either the stamped attribute or the resolved media query — so the
// first client render never disagrees with what's already painted.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem('tapestry-theme');
    if (stored === 'light' || stored === 'dark') {
      document.documentElement.setAttribute('data-theme', stored);
    }
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Same Google Fonts approach as the prototype (Inter + JetBrains Mono) — a plain stylesheet link rather than next/font/google, so font loading has no build-time network dependency. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
        />
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
