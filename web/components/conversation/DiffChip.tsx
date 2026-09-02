import Link from "next/link";
import { ChevronRightIcon, FolderIcon } from "@/components/ui/icons";

export interface DiffChipProps {
  conversationId: string;
  taskId: string;
  files: number;
  add: number;
  del: number;
}

/**
 * Inline "N files changed +add -del" summary chip. Links to the full
 * diff/code-review screen at /conversation/[id]/diff/[taskId] (built by
 * a sibling agent) — this component only needs to produce the correct
 * href, not the destination screen itself.
 */
export function DiffChip({ conversationId, taskId, files, add, del }: DiffChipProps) {
  return (
    <Link href={`/conversation/${conversationId}/diff/${taskId}`} className="diff-chip">
      <FolderIcon size={14} />
      <span>{files} {files === 1 ? "file" : "files"} changed</span>
      <span className="plus">+{add}</span>
      <span className="minus">-{del}</span>
      <ChevronRightIcon size={13} />
    </Link>
  );
}
