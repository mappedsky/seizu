import { memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Menu,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
} from '@mui/material';
import ChecklistIcon from '@mui/icons-material/Checklist';
import CodeIcon from '@mui/icons-material/Code';
import DataObjectIcon from '@mui/icons-material/DataObject';
import DataArrayIcon from '@mui/icons-material/DataArray';
import FormatBoldIcon from '@mui/icons-material/FormatBold';
import FormatItalicIcon from '@mui/icons-material/FormatItalic';
import FormatListBulletedIcon from '@mui/icons-material/FormatListBulleted';
import FormatListNumberedIcon from '@mui/icons-material/FormatListNumbered';
import FormatQuoteIcon from '@mui/icons-material/FormatQuote';
import FormatStrikethroughIcon from '@mui/icons-material/FormatStrikethrough';
import HorizontalRuleIcon from '@mui/icons-material/HorizontalRule';
import LinkIcon from '@mui/icons-material/Link';
import LinkOffIcon from '@mui/icons-material/LinkOff';
import RedoIcon from '@mui/icons-material/Redo';
import TableChartIcon from '@mui/icons-material/TableChart';
import UndoIcon from '@mui/icons-material/Undo';
import { EditorContent, useEditor } from '@tiptap/react';
import type { Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Table } from '@tiptap/extension-table';
import { TableCell } from '@tiptap/extension-table-cell';
import { TableHeader } from '@tiptap/extension-table-header';
import { TableRow } from '@tiptap/extension-table-row';
import { TaskItem } from '@tiptap/extension-task-item';
import { TaskList } from '@tiptap/extension-task-list';
import { Markdown } from 'tiptap-markdown';
import type { MarkdownStorage } from 'tiptap-markdown';

import { MarkdocVariable } from 'src/components/markdoc/MarkdocVariableNode';
import { getCspNonce } from 'src/cspNonce';
import { fontFamilyMono } from 'src/theme/typography';

export interface MarkdocVariableOption {
  name: string;
  label?: string;
}

type Mode = 'wysiwyg' | 'source';
type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

interface MarkdownEditorProps {
  value: string | undefined;
  onChange: (value: string | undefined) => void;
  sourceLabel?: string;
  availableVariables?: MarkdocVariableOption[];
}

function getMarkdown(editor: Editor): string {
  const storage = (editor.storage as unknown as Record<string, unknown>)
    .markdown as MarkdownStorage | undefined;
  return storage?.getMarkdown() ?? '';
}

function getHeadingValue(editor: Editor | null): string {
  if (!editor) return 'paragraph';
  for (let level = 1; level <= 6; level++) {
    if (editor.isActive('heading', { level })) return String(level);
  }
  return 'paragraph';
}

interface ToolbarButtonProps {
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}

function ToolbarButton({
  label,
  onClick,
  active,
  disabled,
  children,
}: ToolbarButtonProps) {
  return (
    <Tooltip title={label}>
      <span>
        <IconButton
          aria-label={label}
          aria-pressed={active ?? undefined}
          size="small"
          onClick={onClick}
          disabled={disabled}
          color={active ? 'primary' : 'default'}
        >
          {children}
        </IconButton>
      </span>
    </Tooltip>
  );
}

function MarkdownEditorInner({
  value,
  onChange,
  sourceLabel = 'Markdown content',
  availableVariables = [],
}: MarkdownEditorProps) {
  const [mode, setMode] = useState<Mode>('wysiwyg');
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const tableButtonRef = useRef<HTMLButtonElement | null>(null);
  const [tableMenuOpen, setTableMenuOpen] = useState(false);
  const variableButtonRef = useRef<HTMLButtonElement | null>(null);
  const [variableMenuOpen, setVariableMenuOpen] = useState(false);
  const [, setTick] = useState(0);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        link: { openOnClick: false, autolink: true },
      }),
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      TaskList,
      TaskItem.configure({ nested: true }),
      MarkdocVariable,
      Markdown.configure({
        // MarkdocVariable's parse.setup registers a markdown-it inline rule
        // that handles `{%$name%}` directly, so we don't need html:true to
        // bridge through HTML spans. Keeping html:false means stray `<div>`
        // / `<script>` etc. pasted into source mode are escaped as text
        // rather than rendered.
        html: false,
        tightLists: true,
        breaks: false,
      }),
    ],
    // Tiptap's column-resize plugin (and a few other utilities) inject a
    // <style> tag at runtime via `createStyleTag`. Pass our CSP nonce so
    // the tag satisfies `style-src 'self' 'nonce-...'`.
    injectNonce: getCspNonce(),
    content: value ?? '',
    onUpdate: ({ editor: e }) => {
      const md = getMarkdown(e);
      onChange(md.trim() ? md : undefined);
    },
  });

  useEffect(() => {
    if (!editor) return undefined;
    const bump = () => setTick((n) => n + 1);
    editor.on('transaction', bump);
    editor.on('selectionUpdate', bump);
    return () => {
      editor.off('transaction', bump);
      editor.off('selectionUpdate', bump);
    };
  }, [editor]);

  useEffect(() => {
    if (!editor || mode !== 'wysiwyg') return;
    const current = getMarkdown(editor);
    if ((value ?? '') !== current) {
      editor.commands.setContent(value ?? '', { emitUpdate: false });
    }
    // We only want to resync when switching back to WYSIWYG; intentionally omit `value`.
  }, [mode, editor]);

  function openLinkDialog() {
    if (!editor) return;
    const previous =
      (editor.getAttributes('link').href as string | undefined) ?? '';
    setLinkUrl(previous);
    setLinkDialogOpen(true);
  }

  function applyLink() {
    if (!editor) return;
    const url = linkUrl.trim();
    if (url) {
      editor
        .chain()
        .focus()
        .extendMarkRange('link')
        .setLink({ href: url })
        .run();
    } else {
      editor.chain().focus().extendMarkRange('link').unsetLink().run();
    }
    setLinkDialogOpen(false);
  }

  function removeLink() {
    if (!editor) return;
    editor.chain().focus().extendMarkRange('link').unsetLink().run();
  }

  function applyHeading(val: string) {
    if (!editor) return;
    if (val === 'paragraph') {
      editor.chain().focus().setParagraph().run();
    } else {
      editor
        .chain()
        .focus()
        .toggleHeading({ level: parseInt(val, 10) as HeadingLevel })
        .run();
    }
  }

  const inTable = editor?.isActive('table') ?? false;

  function runTableCommand(
    cmd: (chain: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>,
  ) {
    if (!editor) return;
    cmd(editor.chain().focus()).run();
    setTableMenuOpen(false);
  }

  const sortedVariables = useMemo(
    () => [...availableVariables].sort((a, b) => a.name.localeCompare(b.name)),
    [availableVariables],
  );

  function insertVariable(name: string) {
    if (!editor) return;
    editor.chain().focus().insertMarkdocVariable({ name }).run();
    setVariableMenuOpen(false);
  }

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1 }}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, val: Mode | null) => {
            if (val) setMode(val);
          }}
          aria-label="Editor mode"
        >
          <ToggleButton value="wysiwyg" aria-label="WYSIWYG editor">
            WYSIWYG
          </ToggleButton>
          <ToggleButton value="source" aria-label="Markdown source">
            Markdown
          </ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      {mode === 'wysiwyg' ? (
        <Box>
          <Box
            sx={{
              position: 'sticky',
              top: 0,
              zIndex: 1,
              bgcolor: 'background.paper',
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              gap: 0.25,
              mb: 1,
              p: 0.5,
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 1,
            }}
          >
            <ToolbarButton
              label="Bold"
              active={editor?.isActive('bold') ?? false}
              onClick={() => editor?.chain().focus().toggleBold().run()}
              disabled={!editor}
            >
              <FormatBoldIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Italic"
              active={editor?.isActive('italic') ?? false}
              onClick={() => editor?.chain().focus().toggleItalic().run()}
              disabled={!editor}
            >
              <FormatItalicIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Strikethrough"
              active={editor?.isActive('strike') ?? false}
              onClick={() => editor?.chain().focus().toggleStrike().run()}
              disabled={!editor}
            >
              <FormatStrikethroughIcon fontSize="small" />
            </ToolbarButton>
            <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
            <Select
              slotProps={{ input: { 'aria-label': 'Heading level' } }}
              value={getHeadingValue(editor)}
              onChange={(e) => applyHeading(e.target.value as string)}
              size="small"
              disabled={!editor}
              sx={{
                fontSize: '0.8rem',
                minWidth: 120,
                height: 30,
                '& .MuiSelect-select': { py: '4px' },
              }}
            >
              <MenuItem value="paragraph" sx={{ fontSize: '0.85rem' }}>
                Normal text
              </MenuItem>
              <MenuItem value="1" sx={{ fontSize: '0.85rem' }}>
                Heading 1
              </MenuItem>
              <MenuItem value="2" sx={{ fontSize: '0.85rem' }}>
                Heading 2
              </MenuItem>
              <MenuItem value="3" sx={{ fontSize: '0.85rem' }}>
                Heading 3
              </MenuItem>
              <MenuItem value="4" sx={{ fontSize: '0.85rem' }}>
                Heading 4
              </MenuItem>
              <MenuItem value="5" sx={{ fontSize: '0.85rem' }}>
                Heading 5
              </MenuItem>
              <MenuItem value="6" sx={{ fontSize: '0.85rem' }}>
                Heading 6
              </MenuItem>
            </Select>
            <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
            <ToolbarButton
              label="Bullet list"
              active={editor?.isActive('bulletList') ?? false}
              onClick={() => editor?.chain().focus().toggleBulletList().run()}
              disabled={!editor}
            >
              <FormatListBulletedIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Ordered list"
              active={editor?.isActive('orderedList') ?? false}
              onClick={() => editor?.chain().focus().toggleOrderedList().run()}
              disabled={!editor}
            >
              <FormatListNumberedIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Task list"
              active={editor?.isActive('taskList') ?? false}
              onClick={() => editor?.chain().focus().toggleTaskList().run()}
              disabled={!editor}
            >
              <ChecklistIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Blockquote"
              active={editor?.isActive('blockquote') ?? false}
              onClick={() => editor?.chain().focus().toggleBlockquote().run()}
              disabled={!editor}
            >
              <FormatQuoteIcon fontSize="small" />
            </ToolbarButton>
            <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
            <ToolbarButton
              label="Inline code"
              active={editor?.isActive('code') ?? false}
              onClick={() => editor?.chain().focus().toggleCode().run()}
              disabled={!editor}
            >
              <CodeIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Code block"
              active={editor?.isActive('codeBlock') ?? false}
              onClick={() => editor?.chain().focus().toggleCodeBlock().run()}
              disabled={!editor}
            >
              <DataObjectIcon fontSize="small" />
            </ToolbarButton>
            <Tooltip
              title={
                sortedVariables.length === 0
                  ? 'No variables available'
                  : 'Insert variable'
              }
            >
              <span>
                <IconButton
                  ref={variableButtonRef}
                  aria-label="Insert variable"
                  aria-haspopup="menu"
                  aria-expanded={variableMenuOpen || undefined}
                  size="small"
                  onClick={() => setVariableMenuOpen(true)}
                  disabled={!editor || sortedVariables.length === 0}
                >
                  <DataArrayIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Menu
              anchorEl={variableButtonRef.current}
              open={variableMenuOpen}
              onClose={() => setVariableMenuOpen(false)}
              slotProps={{ paper: { sx: { minWidth: 220 } } }}
            >
              {sortedVariables.map((v) => (
                <MenuItem key={v.name} onClick={() => insertVariable(v.name)}>
                  <Box
                    sx={{
                      fontFamily: fontFamilyMono,
                      fontSize: '0.85rem',
                      color: 'primary.main',
                      mr: 1,
                    }}
                  >
                    {`$${v.name}`}
                  </Box>
                  {v.label && (
                    <Box sx={{ color: 'text.secondary', fontSize: '0.8rem' }}>
                      {v.label}
                    </Box>
                  )}
                </MenuItem>
              ))}
            </Menu>
            <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
            <ToolbarButton
              label="Link"
              active={editor?.isActive('link') ?? false}
              onClick={openLinkDialog}
              disabled={!editor}
            >
              <LinkIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Remove link"
              onClick={removeLink}
              disabled={!editor || !editor.isActive('link')}
            >
              <LinkOffIcon fontSize="small" />
            </ToolbarButton>
            <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
            <Tooltip title="Table">
              <span>
                <IconButton
                  ref={tableButtonRef}
                  aria-label="Table"
                  aria-haspopup="menu"
                  aria-expanded={tableMenuOpen || undefined}
                  size="small"
                  color={inTable ? 'primary' : 'default'}
                  onClick={() => setTableMenuOpen(true)}
                  disabled={!editor}
                >
                  <TableChartIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Menu
              anchorEl={tableButtonRef.current}
              open={tableMenuOpen}
              onClose={() => setTableMenuOpen(false)}
              slotProps={{ paper: { sx: { minWidth: 200 } } }}
            >
              <MenuItem
                onClick={() =>
                  runTableCommand((c) =>
                    c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }),
                  )
                }
              >
                Insert table
              </MenuItem>
              <Divider />
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.addRowAfter())}
              >
                Add row below
              </MenuItem>
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.addRowBefore())}
              >
                Add row above
              </MenuItem>
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.deleteRow())}
              >
                Delete row
              </MenuItem>
              <Divider />
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.addColumnAfter())}
              >
                Add column after
              </MenuItem>
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.addColumnBefore())}
              >
                Add column before
              </MenuItem>
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.deleteColumn())}
              >
                Delete column
              </MenuItem>
              <Divider />
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.toggleHeaderRow())}
              >
                Toggle header row
              </MenuItem>
              <MenuItem
                disabled={!inTable}
                onClick={() => runTableCommand((c) => c.deleteTable())}
                sx={{ color: 'error.main' }}
              >
                Delete table
              </MenuItem>
            </Menu>
            <ToolbarButton
              label="Horizontal rule"
              onClick={() => editor?.chain().focus().setHorizontalRule().run()}
              disabled={!editor}
            >
              <HorizontalRuleIcon fontSize="small" />
            </ToolbarButton>
            <Divider orientation="vertical" flexItem sx={{ mx: 0.5 }} />
            <ToolbarButton
              label="Undo"
              onClick={() => editor?.chain().focus().undo().run()}
              disabled={!editor?.can().undo()}
            >
              <UndoIcon fontSize="small" />
            </ToolbarButton>
            <ToolbarButton
              label="Redo"
              onClick={() => editor?.chain().focus().redo().run()}
              disabled={!editor?.can().redo()}
            >
              <RedoIcon fontSize="small" />
            </ToolbarButton>
          </Box>
          <Box
            sx={{
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 1,
              p: 1.5,
              minHeight: 200,
              '& .ProseMirror': {
                outline: 'none',
                minHeight: 180,
                '& > *:first-of-type': { mt: 0 },
                '& > *:last-child': { mb: 0 },
                '& p': { my: 1 },
                '& h1, & h2, & h3, & h4, & h5, & h6': { mt: 2, mb: 1 },
                '& ul, & ol': { pl: 3, my: 1 },
                '& ul[data-type="taskList"]': {
                  listStyle: 'none',
                  pl: 0,
                  '& p': { my: 0 },
                  '& li': {
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    '& > label': {
                      flexShrink: 0,
                      display: 'flex',
                      alignItems: 'center',
                      cursor: 'pointer',
                      userSelect: 'none',
                    },
                    '& > div': { flex: 1 },
                  },
                },
                '& blockquote': {
                  borderLeft: '4px solid',
                  borderColor: 'divider',
                  pl: 2,
                  my: 1,
                  color: 'text.secondary',
                },
                '& code': {
                  fontFamily: fontFamilyMono,
                  bgcolor: 'action.hover',
                  px: 0.5,
                  py: 0.25,
                  borderRadius: 0.5,
                },
                '& pre': {
                  fontFamily: fontFamilyMono,
                  bgcolor: 'action.hover',
                  p: 1.5,
                  borderRadius: 1,
                  overflow: 'auto',
                  my: 1,
                },
                '& pre code': { bgcolor: 'transparent', p: 0 },
                '& a': { color: 'primary.main', textDecoration: 'underline' },
                '& hr': {
                  border: 'none',
                  borderTop: '2px solid',
                  borderColor: 'divider',
                  my: 2,
                },
                '& table': {
                  borderCollapse: 'collapse',
                  tableLayout: 'fixed',
                  width: '100%',
                  my: 1,
                  overflow: 'hidden',
                },
                '& table td, & table th': {
                  border: '1px solid',
                  borderColor: 'divider',
                  padding: '6px 10px',
                  minWidth: '80px',
                  verticalAlign: 'top',
                  position: 'relative',
                },
                '& table th': {
                  bgcolor: 'action.hover',
                  fontWeight: 700,
                  textAlign: 'left',
                },
                '& table .selectedCell': {
                  bgcolor: 'action.selected',
                },
                '& table .column-resize-handle': {
                  position: 'absolute',
                  right: '-2px',
                  top: 0,
                  bottom: '-2px',
                  width: '4px',
                  bgcolor: 'primary.main',
                  pointerEvents: 'none',
                },
                '&.resize-cursor': { cursor: 'col-resize' },
              },
            }}
          >
            <EditorContent editor={editor} />
          </Box>
        </Box>
      ) : (
        <TextField
          fullWidth
          size="small"
          label={sourceLabel}
          multiline
          minRows={6}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value || undefined)}
        />
      )}

      <Dialog
        open={linkDialogOpen}
        onClose={() => setLinkDialogOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Insert link</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="URL"
            value={linkUrl}
            onChange={(e) => setLinkUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                applyLink();
              }
            }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLinkDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={applyLink}>
            Apply
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

const MarkdownEditor = memo(MarkdownEditorInner);
export default MarkdownEditor;
