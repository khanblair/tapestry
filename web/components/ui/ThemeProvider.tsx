"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useTheme, type ThemeName } from "@/lib/theme";
import { MoonIcon, SunIcon } from "./icons";

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (next: ThemeName) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const value = useTheme();
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/** Reads the theme context set up by <ThemeProvider>. */
export function useThemeContext(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useThemeContext must be used within a <ThemeProvider>");
  }
  return ctx;
}

/**
 * Ready-made light/dark toggle button, matching the prototype's toolbar
 * sun/moon control. Any screen (e.g. the Appearance settings panel) can
 * render this directly, or call useThemeContext() to build its own UI.
 */
export function ThemeToggleButton() {
  const { theme, toggleTheme } = useThemeContext();
  return (
    <button
      type="button"
      className="icon-btn"
      onClick={toggleTheme}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
    >
      {theme === "dark" ? <SunIcon size={18} /> : <MoonIcon size={18} />}
    </button>
  );
}
