import { visit } from "unist-util-visit";
import type { Root, Text } from "mdast";

/**
 * A remark plugin that splits `@handle` out of plain text nodes and wraps
 * each one in a real `<span className="mention">` element, so `@rex`
 * highlights correctly even inside **bold** text or a list item --
 * running as an AST transform (before react-markdown ever produces DOM)
 * is what lets that compose with markdown rendering instead of the two
 * fighting over the same text.
 *
 * The wrapper node sets `data.hName`/`data.hProperties` rather than
 * introducing a bespoke mdast node type (e.g. `{type: "mention", ...}`)
 * -- that was this plugin's first, broken attempt: `mdast-util-to-hast`
 * (which react-markdown uses internally to turn the mdast tree into the
 * hast tree it actually renders) only converts a node into an element
 * via `data.hName`/`data.hProperties`, or via its small set of built-in
 * mdast node types (paragraph, strong, text, ...) -- an unrecognized
 * custom type name is silently dropped by `mdast-util-to-hast`'s
 * `unknownHandler` before react-markdown's `components` map ever sees
 * it, so no `mention` entry there could ever have rendered anything. Once
 * the node is a real hast `span`, react-markdown renders it natively --
 * no `components` override needed for it at all.
 */
const MENTION_RE = /@(\w+)/g;

interface MentionNode {
  type: "mention";
  data: { hName: "span"; hProperties: { className: string } };
  children: [Text];
}

export function remarkMentions() {
  return (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (!parent || index === undefined || !MENTION_RE.test(node.value)) return;
      MENTION_RE.lastIndex = 0;

      const children: Array<Text | MentionNode> = [];
      let lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = MENTION_RE.exec(node.value)) !== null) {
        if (match.index > lastIndex) {
          children.push({ type: "text", value: node.value.slice(lastIndex, match.index) });
        }
        children.push({
          type: "mention",
          data: { hName: "span", hProperties: { className: "mention" } },
          children: [{ type: "text", value: match[0] }],
        });
        lastIndex = match.index + match[0].length;
      }
      if (lastIndex < node.value.length) {
        children.push({ type: "text", value: node.value.slice(lastIndex) });
      }

      parent.children.splice(index, 1, ...(children as never[]));
      return index + children.length;
    });
  };
}
