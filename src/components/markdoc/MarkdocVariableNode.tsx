import { Node, mergeAttributes } from '@tiptap/core';
import { NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react';
import type { NodeViewProps } from '@tiptap/react';
import { Chip } from '@mui/material';
import type MarkdownIt from 'markdown-it';
import { fontFamilyMono } from 'src/theme/typography';

// Recognise compact `{%$name%}` and the spaced form `{% $name %}` — the
// renderer accepts both on input, but the editor's serializer always writes
// the compact form so links like `[x](https://.../{%$foo%})` round-trip
// through markdown-it without breaking.
const MARKDOC_VAR_INLINE_RE = /^\{%\s*\$([a-z][a-z0-9_]*)\s*%\}/;
const OPEN_BRACE = 0x7b; // '{'
const PERCENT = 0x25; // '%'

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    markdocVariable: {
      insertMarkdocVariable: (attrs: { name: string }) => ReturnType;
    };
  }
}

// Register a markdown-it inline rule that turns `{%$name%}` into a
// `markdoc_variable` token, plus a renderer rule that emits the HTML span our
// `parseHTML` matcher picks up. Exported so the unit test can exercise it
// without spinning up a Tiptap editor.
export function setupMarkdocVariableMarkdownIt(md: MarkdownIt): void {
  md.inline.ruler.before('emphasis', 'markdoc_variable', (state, silent) => {
    if (state.src.charCodeAt(state.pos) !== OPEN_BRACE) return false;
    if (state.src.charCodeAt(state.pos + 1) !== PERCENT) return false;
    const match = MARKDOC_VAR_INLINE_RE.exec(state.src.slice(state.pos));
    if (!match) return false;
    if (!silent) {
      const token = state.push('markdoc_variable', '', 0);
      token.meta = { name: match[1] };
    }
    state.pos += match[0].length;
    return true;
  });
  md.renderer.rules.markdoc_variable = (tokens, idx) => {
    const meta = tokens[idx].meta as { name?: string } | null;
    const name = meta?.name ?? '';
    return `<span data-markdoc-var="${name}"></span>`;
  };
}

function MarkdocVariableChip({ node }: NodeViewProps) {
  const name = (node.attrs.name as string | undefined) ?? '';
  return (
    <NodeViewWrapper
      as="span"
      style={{ display: 'inline-block', verticalAlign: 'baseline' }}
    >
      <Chip
        size="small"
        label={`$${name}`}
        sx={{
          fontFamily: fontFamilyMono,
          height: 22,
          fontSize: '0.78rem',
          bgcolor: 'action.hover',
          color: 'primary.main',
          border: '1px solid',
          borderColor: 'divider',
          '& .MuiChip-label': { px: 0.75 },
        }}
      />
    </NodeViewWrapper>
  );
}

export const MarkdocVariable = Node.create({
  name: 'markdocVariable',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes() {
    return {
      name: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-markdoc-var') ?? '',
        renderHTML: (attrs) => ({ 'data-markdoc-var': attrs.name as string }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-markdoc-var]' }];
  },

  renderHTML({ HTMLAttributes, node }) {
    const name = (node.attrs.name as string | undefined) ?? '';
    return [
      'span',
      mergeAttributes(HTMLAttributes, { 'data-markdoc-var': name }),
      `{%$${name}%}`,
    ];
  },

  addNodeView() {
    return ReactNodeViewRenderer(MarkdocVariableChip);
  },

  addCommands() {
    return {
      insertMarkdocVariable:
        (attrs) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs }),
    };
  },

  addStorage() {
    return {
      markdown: {
        serialize(
          state: { write: (s: string) => void },
          node: { attrs: { name: string } },
        ) {
          // Compact form (no spaces) is required so markdown-it can parse
          // links like `[label](https://example.com/{%$foo%})` — markdown-it
          // bails on URLs containing spaces.
          state.write(`{%$${node.attrs.name}%}`);
        },
        parse: {
          setup(this: { editor: unknown; options: unknown }, md: MarkdownIt) {
            setupMarkdocVariableMarkdownIt(md);
          },
        },
      },
    };
  },
});

export default MarkdocVariable;
