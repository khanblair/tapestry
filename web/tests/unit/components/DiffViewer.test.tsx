import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DiffViewer } from "@/components/diff/DiffViewer";
import type { DiffFile } from "@/lib/mockData";

afterEach(() => cleanup());

const FILES: DiffFile[] = [
  {
    name: "oauth/google.py",
    lines: [
      { type: "ctx", lineNumber: 40, content: "def build_auth_url(self):" },
      { type: "del", lineNumber: 41, content: 'scope = "openid profile email profile.write"' },
      { type: "add", lineNumber: 41, content: 'scope = "openid email profile"' },
    ],
  },
  {
    name: "oauth/routes.py",
    lines: [{ type: "add", lineNumber: 13, content: 'def google_callback(code: str):' }],
  },
];

describe("DiffViewer", () => {
  it("shows the file-count and +additions/-deletions summary", () => {
    render(<DiffViewer files={FILES} additions={142} deletions={8} />);
    expect(screen.getByText("2 files")).toBeInTheDocument();
    expect(screen.getByText("+142")).toBeInTheDocument();
    expect(screen.getByText("-8")).toBeInTheDocument();
  });

  it("renders one tab per file, with the first file active and its lines shown by default", () => {
    render(<DiffViewer files={FILES} additions={142} deletions={8} />);
    expect(screen.getByRole("tab", { name: "oauth/google.py" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "oauth/routes.py" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByText(/openid profile email profile\.write/)).toBeInTheDocument();
  });

  it("switches the visible file when a different tab is clicked", () => {
    render(<DiffViewer files={FILES} additions={142} deletions={8} />);
    fireEvent.click(screen.getByRole("tab", { name: "oauth/routes.py" }));

    expect(screen.getByRole("tab", { name: "oauth/routes.py" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "oauth/google.py" })).toHaveAttribute("aria-selected", "false");
    expect(screen.queryByText(/openid profile email profile\.write/)).not.toBeInTheDocument();
    expect(screen.getByText(/google_callback/)).toBeInTheDocument();
  });

  it("renders add/del/context lines with the matching CSS classes and +/-/space sign", () => {
    const { container } = render(<DiffViewer files={FILES} additions={142} deletions={8} />);
    expect(container.querySelector(".diff-line.add")).toHaveTextContent('+ scope = "openid email profile"');
    expect(container.querySelector(".diff-line.del")).toHaveTextContent('- scope = "openid profile email profile.write"');
    expect(container.querySelector(".diff-line.ctx")).toHaveTextContent("def build_auth_url(self):");
  });
});
