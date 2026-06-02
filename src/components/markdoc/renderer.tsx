// Security: keep Markdoc rendering safe by upholding these invariants:
//   1. Do not pass a `partials` config sourced from user input — `{% partial %}`
//      becomes a content-inclusion primitive otherwise.
//   2. Do not register custom tags that route variable values into `style`,
//      `dangerouslySetInnerHTML`, or URL props (`href`, `src`, `srcDoc`)
//      without validating the value. Variables flow in as React text by default,
//      which React escapes; attribute positions bypass that escaping. The link
//      and image node overrides below allow variable substitution into href/src
//      but apply an allowlist (see `safeSubstitutedUrl`) — keep the allowlist
//      tight and add new schemes only with explicit review.
//   3. The Markdoc tokenizer keeps markdown-it `html: false`, so raw HTML in
//      source content is rendered as escaped text, not executed. Don't enable
//      `html: true` on the renderer.
import * as React from 'react';
import Markdoc, { Tag, nodes } from '@markdoc/markdoc';
import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { brand } from 'src/theme/brand';

function MarkdocTable({ children }: { children?: React.ReactNode }) {
  return (
    <TableContainer component={Paper} variant="outlined" sx={{ my: 2 }}>
      <Table size="small" sx={{ borderCollapse: 'collapse' }}>
        {children}
      </Table>
    </TableContainer>
  );
}

function MarkdocHeadCell({ children }: { children?: React.ReactNode }) {
  return (
    <TableCell
      component="th"
      scope="col"
      sx={{
        whiteSpace: 'normal',
        border: '1px solid',
        borderColor: 'divider',
        fontWeight: 700,
        bgcolor: 'action.hover',
      }}
    >
      {children}
    </TableCell>
  );
}

function MarkdocCell({ children }: { children?: React.ReactNode }) {
  return (
    <TableCell
      sx={{ whiteSpace: 'normal', border: '1px solid', borderColor: 'divider' }}
    >
      {children}
    </TableCell>
  );
}

function MarkdocDetails({
  summary = '',
  children,
}: {
  summary?: string;
  children?: React.ReactNode;
}) {
  return (
    <Box
      component="details"
      sx={{
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        my: 1.25,
        px: 1.5,
      }}
    >
      <Typography
        component="summary"
        sx={{
          cursor: 'pointer',
          fontWeight: 600,
          fontSize: 'inherit',
          lineHeight: 'inherit',
          py: 1,
          userSelect: 'none',
          '&:hover': { opacity: 0.8 },
        }}
      >
        {summary}
      </Typography>
      <Box sx={{ pb: 0.5 }}>{children}</Box>
    </Box>
  );
}

function MarkdocContinuation() {
  return (
    <Box
      aria-hidden="true"
      sx={{
        alignItems: 'center',
        color: (theme) =>
          theme.palette.mode === 'dark' ? brand.starlight : brand.starlightDark,
        display: 'flex',
        gap: 1,
        my: 1.5,
        '&::before, &::after': {
          borderTop: 2,
          borderColor: 'currentColor',
          content: '""',
          flex: 1,
        },
      }}
    >
      <Typography
        component="span"
        sx={{
          color: 'inherit',
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: '0.8em',
          fontWeight: 700,
          lineHeight: 1,
        }}
      >
        ...
      </Typography>
    </Box>
  );
}

// Preprocesses LLM-generated <details>/<summary> HTML blocks into Markdoc
// {% details %} tag syntax so they render as collapsible sections.  Raw HTML
// is blocked by Markdoc's html:false setting (intentional security constraint),
// so this conversion is the safe way to support them.
// eslint-disable-next-line no-control-regex -- intentional: strip control chars for safety
const DETAILS_CONTROL_RE = /[\u0000-\u001F\u007F]/g;
const DETAILS_BLOCK_RE =
  /<details[^>]*>\s*<summary[^>]*>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/gi;

function preprocessDetailsBlocks(source: string): string {
  return source.replace(
    DETAILS_BLOCK_RE,
    (_match, rawSummary: string, body: string) => {
      const summaryText = rawSummary
        .replace(/<[^>]+>/g, '')
        .replace(DETAILS_CONTROL_RE, '')
        .replace(/\\/g, '\\\\')
        .replace(/"/g, '\\"')
        .trim();
      return `{% details summary="${summaryText}" %}\n${body.trim()}\n{% /details %}`;
    },
  );
}

const markdocComponents = {
  MuiLink: Link,
  MuiTable: MarkdocTable,
  MuiTableHead: TableHead,
  MuiTableBody: TableBody,
  MuiTableRow: TableRow,
  MuiTableHeadCell: MarkdocHeadCell,
  MuiTableCell: MarkdocCell,
  MuiDetails: MarkdocDetails,
  MuiContinuation: MarkdocContinuation,
};

const headingNode = {
  ...nodes.heading,
  transform(node: any, config: any) {
    const attributes = node.transformAttributes(config);
    const children = node.transformChildren(config);
    const level = Math.min((node.attributes.level ?? 1) + 1, 6);
    return new Tag(`h${level}`, attributes, children);
  },
};

const BLOCK_TAGS = new Set([
  'article',
  'blockquote',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'MuiTable',
  'ol',
  'p',
  'pre',
  'table',
  'ul',
]);

function hasMeaningfulContent(children: any[]): boolean {
  return children.some((child) => {
    if (typeof child === 'string') return child.trim() !== '';
    if (child == null || child === false) return false;
    if (Tag.isTag(child)) return hasMeaningfulContent(child.children);
    return true;
  });
}

function isBlockTag(child: any): child is Tag {
  return Tag.isTag(child) && BLOCK_TAGS.has(child.name);
}

function isEmptyBlockTag(child: Tag): boolean {
  return child.children.length === 0 || !hasMeaningfulContent(child.children);
}

const paragraphNode = {
  ...nodes.paragraph,
  transform(node: any, config: any) {
    const attributes = node.transformAttributes(config);
    const children = node.transformChildren(config);
    if (!children.some(isBlockTag)) return new Tag('p', attributes, children);

    const result: any[] = [];
    let inlineChildren: any[] = [];
    const flushInline = () => {
      if (hasMeaningfulContent(inlineChildren)) {
        result.push(new Tag('p', attributes, inlineChildren));
      }
      inlineChildren = [];
    };

    children.forEach((child: any) => {
      if (isBlockTag(child)) {
        flushInline();
        if (!isEmptyBlockTag(child)) result.push(child);
      } else {
        inlineChildren.push(child);
      }
    });
    flushInline();

    return result;
  },
};

const listNode = {
  ...nodes.list,
  transform(node: any, config: any) {
    const attributes = node.transformAttributes(config);
    const children = node.transformChildren(config);
    const ordered = node.attributes.ordered;
    const tag = ordered ? 'ol' : 'ul';
    const className = ordered ? 'mui-markdown-ol' : 'mui-markdown-ul';
    return new Tag(tag, { ...attributes, class: className }, children);
  },
};

// Substitute {%$name%} (or {% $name %}) inside markdown link/image URL
// strings. markdown-it tokenizes `[text](url)` into a literal href and does
// not re-scan the URL for Markdoc variables, so we do it here. The URL is
// URL-decoded first because markdown-it percent-encodes `{`, `%`, and spaces
// inside the href.
const URL_VAR_RE = /\{%\s*\$([a-z][a-z0-9_]*)\s*%\}/g;

// Allowlist of protocols permitted in URLs that were *modified* by variable
// substitution. Variable values come from end-user input (URL query string,
// autocomplete pick), so we apply a stricter check than markdown-it's
// blacklist: deny anything not explicitly permitted, including custom OS
// protocol handlers (slack://, vscode://, etc.) and any future scheme.
// Static editor-authored URLs are not re-validated here — they already passed
// markdown-it's validateLink at parse time.
const ALLOWED_SUBSTITUTED_PROTO_RE = /^(https?|mailto|tel):/i;
const SCHEME_RE = /^[a-z][a-z0-9+\-.]*:/i;
const SAFE_DATA_IMAGE_RE = /^data:image\/(gif|png|jpeg|webp);/i;

// Browsers (per the WHATWG URL spec) strip ASCII tab, LF, CR, and other C0
// control characters from `href` attributes at navigation time. Mirror that
// stripping before validation so a value like "java\tscript:alert(1)" cannot
// slip past the scheme check (the embedded tab makes SCHEME_RE fail to match,
// so it would otherwise be treated as a relative URL) and then resolve to
// `javascript:` in the browser after the tab is removed. We return the
// normalized form so the rendered href is also clean.
// eslint-disable-next-line no-control-regex -- intentionally strips control chars
const URL_CONTROL_CHARS_RE = /[\u0000-\u001F\u007F]/g;

function safeSubstitutedUrl(url: string): string {
  const normalized = url.replace(URL_CONTROL_CHARS_RE, '').trim();
  if (!normalized) return '#';
  if (normalized.startsWith('//')) return '#';
  // Relative URLs (no scheme) are always safe — they resolve under the
  // current page's origin.
  if (!SCHEME_RE.test(normalized)) return normalized;
  if (ALLOWED_SUBSTITUTED_PROTO_RE.test(normalized)) return normalized;
  if (SAFE_DATA_IMAGE_RE.test(normalized)) return normalized;
  return '#';
}

function safeUntrustedUrl(url: string, { image }: { image: boolean }): string {
  const normalized = url.replace(URL_CONTROL_CHARS_RE, '').trim();
  if (!normalized) return '#';
  if (normalized.startsWith('//')) return '#';
  if (!SCHEME_RE.test(normalized)) return normalized;
  if (image) return /^https?:/i.test(normalized) ? normalized : '#';
  return ALLOWED_SUBSTITUTED_PROTO_RE.test(normalized) ? normalized : '#';
}

function substituteUrlVars(
  url: string,
  variables: Record<string, unknown>,
): { value: string; changed: boolean } {
  let decoded: string;
  try {
    decoded = decodeURIComponent(url);
  } catch {
    decoded = url;
  }
  let changed = false;
  const value = decoded.replace(URL_VAR_RE, (_match, name) => {
    changed = true;
    const v = variables[name];
    return v != null ? String(v) : '';
  });
  return { value, changed };
}

const linkNode = {
  ...nodes.link,
  transform(node: any, config: any) {
    const attributes = node.transformAttributes(config);
    const children = node.transformChildren(config);
    if (typeof attributes.href === 'string') {
      const { value, changed } = substituteUrlVars(
        attributes.href,
        config.variables ?? {},
      );
      if (changed) attributes.href = safeSubstitutedUrl(value);
      else if (config.untrustedUrls) {
        attributes.href = safeUntrustedUrl(attributes.href, { image: false });
      }
    }
    return new Tag(
      'MuiLink',
      { underline: 'hover', color: 'primary', ...attributes },
      children,
    );
  },
};

const imageNode = {
  ...nodes.image,
  transform(node: any, config: any) {
    const attributes = node.transformAttributes(config);
    if (typeof attributes.src === 'string') {
      const { value, changed } = substituteUrlVars(
        attributes.src,
        config.variables ?? {},
      );
      if (changed) attributes.src = safeSubstitutedUrl(value);
      else if (config.untrustedUrls) {
        attributes.src = safeUntrustedUrl(attributes.src, { image: true });
      }
    }
    return new Tag('img', {
      loading: 'lazy',
      referrerPolicy: 'no-referrer',
      ...attributes,
    });
  },
};

const markdocNodes = {
  heading: headingNode,
  paragraph: paragraphNode,
  list: listNode,
  link: linkNode,
  image: imageNode,
  table: { ...nodes.table, render: 'MuiTable' },
  thead: { ...nodes.thead, render: 'MuiTableHead' },
  tbody: { ...nodes.tbody, render: 'MuiTableBody' },
  tr: { ...nodes.tr, render: 'MuiTableRow' },
  th: { ...nodes.th, render: 'MuiTableHeadCell' },
  td: { ...nodes.td, render: 'MuiTableCell' },
};

const markdocTags = {
  continuation: {
    render: 'MuiContinuation',
    selfClosing: true,
  },
  details: {
    render: 'MuiDetails',
    attributes: {
      summary: { type: String, default: '' },
    },
  },
};

export interface MarkdocRendererProps {
  source: string;
  variables?: Record<string, string>;
  untrustedUrls?: boolean;
}

export function MarkdocRenderer({
  source,
  variables,
  untrustedUrls = false,
}: MarkdocRendererProps) {
  const rendered = React.useMemo(() => {
    const processedSource = preprocessDetailsBlocks(source ?? '');
    const ast = Markdoc.parse(processedSource);
    const content = Markdoc.transform(ast, {
      variables: variables ?? {},
      nodes: markdocNodes,
      tags: markdocTags,
      untrustedUrls,
    });
    return Markdoc.renderers.react(content, React, {
      components: markdocComponents,
    });
  }, [source, variables, untrustedUrls]);

  return <>{rendered}</>;
}
