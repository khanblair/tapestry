"use client";

import { useEffect, type ReactNode } from "react";
import { BackIcon, XIcon } from "./icons";

export interface ModalProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Matches .overlay-panel.wide (min(880px,100%) instead of min(620px,100%)) — used by persona management and the diff screen in the prototype. */
  wide?: boolean;
}

/**
 * The shared overlay shell every "modal screen" (settings, persona
 * management, profile, new-conversation, search, activity, diff) is
 * built on. Renders full-screen on mobile/tablet and a centered
 * backdrop-modal on desktop — CSS-only split, no useMediaQuery hook, so
 * there's no hydration flash: app/globals.css's `.overlay-layer` /
 * `.overlay-panel` rules switch at a single `min-width: 900px` media
 * query (see the breakpoint note at the top of globals.css — this is
 * intentionally NOT the same 768px breakpoint the roster/conversation
 * pane collapse uses).
 *
 * Both the back-arrow (non-desktop) and the X (desktop) are always
 * rendered; `.modal-back` / `.modal-close` toggle which one is visible
 * per breakpoint, so there's exactly one onClose wiring and no branch on
 * viewport width in JS.
 */
export function Modal({ title, onClose, children, wide = false }: ModalProps) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="overlay-layer"
      onClick={(event) => {
        // Desktop backdrop click-to-close. On mobile/tablet the panel
        // fills the layer so there's no backdrop to click.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={`overlay-panel${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="topbar">
          <button type="button" className="icon-btn modal-back" onClick={onClose} aria-label="Back">
            <BackIcon size={18} />
          </button>
          <h2 style={{ flex: 1 }}>{title}</h2>
          <button type="button" className="icon-btn modal-close" onClick={onClose} aria-label="Close">
            <XIcon size={18} />
          </button>
        </div>
        <div className="scroll screen-pad">{children}</div>
      </div>
    </div>
  );
}
