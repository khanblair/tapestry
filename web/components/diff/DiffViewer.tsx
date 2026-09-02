"use client";

// File tabs + line-numbered add/del/context rendering, matching the
// prototype's diffScreen() exactly: `+142 -8` summary line, a horizontally
// scrollable tab strip per changed file, and monospace add/del/context lines
// with green/red wash backgrounds. Colors come from the `--diff-add-bg` /
// `--diff-add-fg` / `--diff-del-bg` / `--diff-del-fg` CSS custom properties
// (defined in app/globals.css, sourced from lib/theme.ts's token work) rather
// than importing them — DiffViewer only ever reads `var(--token)` in its
// className-driven styles, so it can't break if lib/theme.ts's export shape
// changes.
//
// Renders as plain flowed content (no independent scroll region of its own):
// the prototype's diffScreen() gives the tabs/lines their own edge-to-edge,
// non-padded scroll area, pinned independently of the action bar below it.
// Modal.tsx's current contract only offers one `.scroll.screen-pad` body per
// panel (no `flush`/unpadded escape hatch), so DiffViewer and the action bar
// both live inside that single scroll region instead — a known, minor
// fidelity gap from the prototype (tabs and Approve/Reject scroll with the
// content instead of staying pinned), not a functional one.

import { useState } from "react";
import type { DiffFile } from "@/lib/mockData";

export interface DiffViewerProps {
  files: DiffFile[];
  additions: number;
  deletions: number;
  /** Defaults to files.length; pass explicitly if the summary should reflect files not included in `files` (e.g. binary files). */
  fileCount?: number;
  initialTab?: number;
}

export function DiffViewer({ files, additions, deletions, fileCount, initialTab = 0 }: DiffViewerProps) {
  const [activeTab, setActiveTab] = useState(Math.min(initialTab, Math.max(files.length - 1, 0)));
  const file = files[activeTab];
  const count = fileCount ?? files.length;

  return (
    <div className="diff-viewer">
      <div className="crumbs" style={{ padding: "10px 14px 0" }}>
        <span>{count} file{count === 1 ? "" : "s"}</span>
        <span>·</span>
        <span className="plus" style={{ color: "var(--diff-add-fg)" }}>
          +{additions}
        </span>
        <span className="minus" style={{ color: "var(--diff-del-fg)" }}>
          -{deletions}
        </span>
      </div>

      <div className="diff-tabs" role="tablist" aria-label="Changed files">
        {files.map((f, i) => (
          <button
            key={f.name}
            type="button"
            role="tab"
            aria-selected={i === activeTab}
            className={`diff-tab${i === activeTab ? " active" : ""}`}
            onClick={() => setActiveTab(i)}
          >
            {f.name}
          </button>
        ))}
      </div>

      <div style={{ padding: "14px 0" }} role="tabpanel">
        {file?.lines.map((line, idx) => {
          const sign = line.type === "add" ? "+" : line.type === "del" ? "-" : " ";
          return (
            <div key={idx} className={`diff-line ${line.type}`}>
              <span className="ln">{line.lineNumber}</span>
              <span className="content">
                {sign} {line.content}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
