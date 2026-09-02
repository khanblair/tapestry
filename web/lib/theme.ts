"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeName = "light" | "dark";

/**
 * Typed mirror of the CSS custom properties defined in app/globals.css.
 * Keep values in sync with that file by hand — these are for the rare
 * spot where JS needs a literal token value (e.g. a canvas/SVG fill that
 * can't reach `var(--token)`), not a replacement for using the CSS
 * variables directly in stylesheets/className-based styling.
 */
export interface ThemeTokenSet {
  bg: string;
  surface: string;
  surface2: string;
  surfaceHover: string;
  border: string;
  borderStrong: string;
  text: string;
  textMuted: string;
  textFaint: string;
  accent: string;
  accentFg: string;
  accentWash: string;
  danger: string;
  dangerWash: string;
  warn: string;
  diffAddBg: string;
  diffAddFg: string;
  diffDelBg: string;
  diffDelFg: string;
  statusOnline: string;
  statusBusy: string;
  statusPaused: string;
  statusOffline: string;
  shadow: string;
}

export const THEME_TOKENS: Record<ThemeName, ThemeTokenSet> = {
  light: {
    bg: "#F8FAFC",
    surface: "#FFFFFF",
    surface2: "#F1F5F9",
    surfaceHover: "#E2E8F0",
    border: "#E2E8F0",
    borderStrong: "#CBD5E1",
    text: "#0F172A",
    textMuted: "#64748B",
    textFaint: "#94A3B8",
    accent: "#16A34A",
    accentFg: "#FFFFFF",
    accentWash: "rgba(22,163,74,.10)",
    danger: "#DC2626",
    dangerWash: "rgba(220,38,38,.08)",
    warn: "#D97706",
    diffAddBg: "rgba(22,163,74,.12)",
    diffAddFg: "#15803D",
    diffDelBg: "rgba(220,38,38,.10)",
    diffDelFg: "#B91C1C",
    statusOnline: "#16A34A",
    statusBusy: "#D97706",
    statusPaused: "#64748B",
    statusOffline: "#CBD5E1",
    shadow: "0 8px 30px rgba(15,23,42,.12)",
  },
  dark: {
    bg: "#0F172A",
    surface: "#1B2336",
    surface2: "#161D2E",
    surfaceHover: "#272F42",
    border: "#293349",
    borderStrong: "#475569",
    text: "#F8FAFC",
    textMuted: "#94A3B8",
    textFaint: "#64748B",
    accent: "#22C55E",
    accentFg: "#0B1220",
    accentWash: "rgba(34,197,94,.13)",
    danger: "#EF4444",
    dangerWash: "rgba(239,68,68,.12)",
    warn: "#F59E0B",
    diffAddBg: "rgba(34,197,94,.15)",
    diffAddFg: "#4ADE80",
    diffDelBg: "rgba(239,68,68,.15)",
    diffDelFg: "#F87171",
    statusOnline: "#22C55E",
    statusBusy: "#F59E0B",
    statusPaused: "#94A3B8",
    statusOffline: "#475569",
    shadow: "0 8px 30px rgba(0,0,0,.45)",
  },
};

const STORAGE_KEY = "tapestry-theme";

function isThemeName(value: string | null): value is ThemeName {
  return value === "light" || value === "dark";
}

function getStoredTheme(): ThemeName | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isThemeName(stored) ? stored : null;
  } catch {
    return null;
  }
}

function getSystemTheme(): ThemeName {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Resolves the theme actually painted on this page right now, preferring
 * the `data-theme` attribute the inline bootstrap script (see
 * app/layout.tsx) may already have stamped onto <html>, so the hook's
 * first render never disagrees with what's on screen.
 */
function getResolvedTheme(): ThemeName {
  if (typeof document !== "undefined") {
    const attr = document.documentElement.getAttribute("data-theme");
    if (isThemeName(attr)) return attr;
  }
  return getStoredTheme() ?? getSystemTheme();
}

/**
 * Manages light/dark theme state via the `data-theme` attribute on
 * <html>, persisted to localStorage. Pairs with the three-tier CSS
 * variable structure in app/globals.css (bare :root = light, media-query
 * dark, explicit [data-theme] override).
 */
export function useTheme() {
  const [theme, setThemeState] = useState<ThemeName>(getResolvedTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = useCallback((next: ThemeName) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage unavailable (private mode, storage quota, etc.) —
      // the theme still applies for the current session.
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return { theme, setTheme, toggleTheme };
}
