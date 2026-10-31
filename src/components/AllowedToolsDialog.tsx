import { useMemo, useState, type ReactNode } from 'react';
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputAdornment,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import CloseIcon from '@mui/icons-material/Close';
import SearchIcon from '@mui/icons-material/Search';
import type { ToolCatalogItem } from 'src/hooks/useToolsetsApi';
import { toolDeclaration } from 'src/pluginAuthoring';
import { hueFor } from 'src/theme/brand';
import { fontFamilyMono } from 'src/theme/typography';

type View = 'all' | 'selected';

interface ToolEntry {
  /** What the file carries and the server resolves, not the catalog name. */
  declaration: string;
  label: string;
  enabled: boolean;
  /** Lower-cased text the filter matches against. */
  haystack: string;
}

interface ToolGroup {
  key: string;
  label: string;
  tools: ToolEntry[];
}

function filterTerms(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/**
 * `piece` with a line-break opportunity after each underscore run, so a long
 * declaration wraps between its segments rather than inside a word.
 */
function breakable(piece: string, offset: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  let at = offset;
  for (const segment of piece.split(/(?<=_)(?=[^_])/)) {
    if (at !== offset) nodes.push(<wbr key={`wbr-${at}`} />);
    nodes.push(segment);
    at += segment.length;
  }
  return nodes;
}

/** `text` with every occurrence of any term wrapped in a highlight. */
function Highlight({
  text,
  terms,
  wrapAtUnderscores = false,
}: {
  text: string;
  terms: string[];
  wrapAtUnderscores?: boolean;
}) {
  const theme = useTheme();
  const format = (piece: string, key: number) =>
    wrapAtUnderscores ? breakable(piece, key) : piece;
  if (!terms.length) return <>{format(text, 0)}</>;
  const lower = text.toLowerCase();
  const marked = new Array<boolean>(text.length).fill(false);
  for (const term of terms) {
    for (
      let at = lower.indexOf(term);
      at !== -1;
      at = lower.indexOf(term, at + term.length)
    ) {
      marked.fill(true, at, at + term.length);
    }
  }
  const parts: ReactNode[] = [];
  let start = 0;
  for (let index = 1; index <= text.length; index += 1) {
    if (index === text.length || marked[index] !== marked[start]) {
      const piece = text.slice(start, index);
      parts.push(
        marked[start] ? (
          <Box
            key={start}
            component="mark"
            sx={{
              bgcolor: alpha(hueFor('ember', theme.palette.mode), 0.24),
              borderRadius: '2px',
              color: 'inherit',
            }}
          >
            {format(piece, start)}
          </Box>
        ) : (
          format(piece, start)
        ),
      );
      start = index;
    }
  }
  return <>{parts}</>;
}

/**
 * Picks a skill's `allowed-tools` declarations from the caller's tool catalog,
 * grouped by toolset and narrowed by a free-text filter. Edits are held until
 * Save.
 */
export default function AllowedToolsDialog({
  open,
  tools,
  selected,
  onClose,
  onSave,
}: {
  open: boolean;
  tools: ToolCatalogItem[];
  selected: string[];
  onClose: () => void;
  onSave: (tools: string[]) => void;
}) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'));
  const wide = useMediaQuery(theme.breakpoints.up('md'));
  const [selection, setSelection] = useState(selected);
  const [query, setQuery] = useState('');
  const [view, setView] = useState<View>('all');
  // The toolset the list is narrowed to from the rail; null shows them all.
  const [scope, setScope] = useState<string | null>(null);
  const terms = useMemo(() => filterTerms(query), [query]);
  const chosen = useMemo(() => new Set(selection), [selection]);

  const groups = useMemo<ToolGroup[]>(() => {
    const grouped = new Map<string, ToolGroup>();
    for (const tool of tools) {
      const key = tool.toolset_id || tool.toolset_name;
      const group = grouped.get(key) ?? {
        key,
        label: tool.toolset_name,
        tools: [],
      };
      const declaration = toolDeclaration(tool.mcp_name);
      const label = tool.name || tool.mcp_name;
      group.tools.push({
        declaration,
        label,
        enabled: tool.enabled,
        haystack: `${label} ${declaration} ${tool.toolset_name}`.toLowerCase(),
      });
      grouped.set(key, group);
    }
    return [...grouped.values()]
      .map((group) => ({
        ...group,
        tools: group.tools.sort((left, right) =>
          left.declaration.localeCompare(right.declaration),
        ),
      }))
      .sort((left, right) => left.label.localeCompare(right.label));
  }, [tools]);

  // Every group with the tools the filter and view leave in it, before the
  // rail's scope applies, so the rail can count what each toolset holds.
  const matchedGroups = useMemo(
    () =>
      groups.map((group) => ({
        ...group,
        total: group.tools.length,
        chosen: group.tools.filter((tool) => chosen.has(tool.declaration))
          .length,
        tools: group.tools.filter(
          (tool) =>
            (view === 'all' || chosen.has(tool.declaration)) &&
            terms.every((term) => tool.haystack.includes(term)),
        ),
      })),
    [chosen, groups, terms, view],
  );
  // The rail exists only on wide screens; elsewhere the scope never applies.
  const activeScope =
    wide && groups.some((group) => group.key === scope) ? scope : null;
  const visibleGroups = matchedGroups.filter(
    (group) =>
      group.tools.length > 0 &&
      (activeScope === null || group.key === activeScope),
  );

  const totalTools = groups.reduce((sum, group) => sum + group.tools.length, 0);
  const matchedTools = matchedGroups.reduce(
    (sum, group) => sum + group.tools.length,
    0,
  );
  const shownTools = visibleGroups.reduce(
    (sum, group) => sum + group.tools.length,
    0,
  );
  const filtering = terms.length > 0;
  const scopeLabel = groups.find((group) => group.key === activeScope)?.label;

  const toggle = (name: string) => {
    setSelection((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name].sort(),
    );
  };

  const setMany = (names: string[], include: boolean) => {
    setSelection((current) => {
      const next = new Set(current);
      for (const name of names) {
        if (include) next.add(name);
        else next.delete(name);
      }
      return [...next].sort();
    });
  };

  const primary = theme.palette.primary.main;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      fullScreen={fullScreen}
      slotProps={{
        paper: {
          sx: {
            backgroundImage: 'none',
            ...(!fullScreen && { height: 'min(760px, calc(100% - 64px))' }),
          },
        },
      }}
    >
      <DialogTitle variant="h3" sx={{ pb: 0.5 }}>
        Allowed tools
      </DialogTitle>
      <Box sx={{ px: 3, pb: 2 }}>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ display: { xs: 'none', sm: 'block' }, maxWidth: '68ch' }}
        >
          Choose the tools this skill depends on. Declaring a tool discloses it
          to the agent; it does not grant permission to call it.
        </Typography>
        <TextField
          autoFocus
          fullWidth
          size="small"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            // The first Escape clears the filter; only an empty one closes.
            if (event.key === 'Escape' && query) {
              event.stopPropagation();
              setQuery('');
            }
          }}
          placeholder="Filter by tool, toolset or declaration"
          slotProps={{
            htmlInput: { 'aria-label': 'Filter tools' },
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
              endAdornment: query ? (
                <InputAdornment position="end">
                  <ButtonBase
                    aria-label="Clear filter"
                    onClick={() => setQuery('')}
                    sx={{
                      borderRadius: '50%',
                      color: 'text.secondary',
                      height: 28,
                      width: 28,
                      '&:hover': { color: 'text.primary' },
                      '&.Mui-focusVisible': {
                        outline: `2px solid ${primary}`,
                      },
                    }}
                  >
                    <CloseIcon fontSize="small" />
                  </ButtonBase>
                </InputAdornment>
              ) : undefined,
            },
          }}
          sx={{ mt: { xs: 1, sm: 2 } }}
        />
        <Box
          sx={{
            alignItems: 'center',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 1.5,
            justifyContent: 'space-between',
            mt: 1.5,
          }}
        >
          <ToggleButtonGroup
            exclusive
            size="small"
            value={view}
            onChange={(_, next: View | null) => next && setView(next)}
            aria-label="Show"
          >
            <ToggleButton value="all" sx={{ px: 1.5, textTransform: 'none' }}>
              All
              <Count>{totalTools}</Count>
            </ToggleButton>
            <ToggleButton
              value="selected"
              sx={{ px: 1.5, textTransform: 'none' }}
            >
              Selected
              <Count>{selection.length}</Count>
            </ToggleButton>
          </ToggleButtonGroup>
          <Typography
            variant="caption"
            color="text.secondary"
            aria-live="polite"
            sx={{ fontVariantNumeric: 'tabular-nums' }}
          >
            {filtering
              ? `${shownTools} ${shownTools === 1 ? 'match' : 'matches'}`
              : ''}
          </Typography>
        </Box>
      </Box>

      <DialogContent
        dividers
        sx={{
          display: 'grid',
          gridTemplateColumns:
            wide && totalTools > 0 ? '232px minmax(0, 1fr)' : 'minmax(0, 1fr)',
          overflow: 'hidden',
          p: 0,
        }}
      >
        {wide && totalTools > 0 && (
          <Box
            component="nav"
            aria-label="Toolsets"
            sx={{
              borderRight: 1,
              borderColor: 'divider',
              overflowY: 'auto',
              px: 1,
              py: 1,
            }}
          >
            <RailItem
              label="All toolsets"
              count={matchedTools}
              selected={activeScope === null}
              onClick={() => setScope(null)}
            />
            {matchedGroups.map((group) => (
              <RailItem
                key={group.key}
                label={group.label}
                count={group.tools.length}
                chosen={group.chosen}
                selected={activeScope === group.key}
                onClick={() => setScope(group.key)}
              />
            ))}
          </Box>
        )}
        <Box sx={{ overflowY: 'auto', px: { xs: 1, sm: 2 } }}>
          {totalTools === 0 ? (
            <EmptyState>
              No tools are available from enabled toolsets.
            </EmptyState>
          ) : visibleGroups.length === 0 ? (
            <EmptyState
              action={
                activeScope !== null && matchedTools > 0 ? (
                  <Button size="small" onClick={() => setScope(null)}>
                    Show all toolsets
                  </Button>
                ) : filtering ? (
                  <Button size="small" onClick={() => setQuery('')}>
                    Clear filter
                  </Button>
                ) : (
                  <Button size="small" onClick={() => setView('all')}>
                    Show all tools
                  </Button>
                )
              }
            >
              {filtering
                ? `No ${view === 'selected' ? 'selected ' : ''}tools ${
                    scopeLabel ? `in ${scopeLabel} ` : ''
                  }match “${query.trim()}”.`
                : scopeLabel
                  ? `No ${scopeLabel} tools selected yet.`
                  : 'No tools selected yet.'}
            </EmptyState>
          ) : (
            visibleGroups.map((group) => {
              const names = group.tools.map((tool) => tool.declaration);
              const allChosen = names.every((name) => chosen.has(name));
              return (
                <Box
                  key={group.key}
                  component="section"
                  aria-label={group.label}
                >
                  <Box
                    sx={{
                      alignItems: 'center',
                      bgcolor: 'background.paper',
                      borderBottom: 1,
                      borderColor: 'divider',
                      display: 'flex',
                      gap: 1,
                      minHeight: 48,
                      position: 'sticky',
                      px: 1,
                      top: 0,
                      zIndex: 1,
                    }}
                  >
                    <Typography
                      component="h3"
                      variant="body2"
                      sx={{ flex: 1, fontWeight: 700, minWidth: 0 }}
                      noWrap
                    >
                      <Highlight text={group.label} terms={terms} />
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ fontVariantNumeric: 'tabular-nums' }}
                    >
                      {group.chosen} of {group.total} selected
                    </Typography>
                    {view === 'all' && (
                      <Button
                        size="small"
                        onClick={() => setMany(names, !allChosen)}
                        aria-label={`${allChosen ? 'Clear' : 'Select'} ${
                          filtering ? 'matching' : 'all'
                        } ${group.label} tools`}
                        sx={{ minWidth: 0, whiteSpace: 'nowrap' }}
                      >
                        {allChosen
                          ? 'Clear'
                          : filtering
                            ? `Select ${names.length}`
                            : 'Select all'}
                      </Button>
                    )}
                  </Box>
                  <Box component="ul" sx={{ listStyle: 'none', m: 0, p: 0 }}>
                    {group.tools.map((tool) => (
                      <ToolRow
                        key={tool.declaration}
                        tool={tool}
                        terms={terms}
                        checked={chosen.has(tool.declaration)}
                        onToggle={() => toggle(tool.declaration)}
                      />
                    ))}
                  </Box>
                </Box>
              );
            })
          )}
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          gap: 1,
          px: 3,
          py: 1.5,
          ...(fullScreen && {
            pb: 'calc(12px + env(safe-area-inset-bottom))',
          }),
        }}
      >
        <Typography
          variant="body2"
          sx={{ flex: 1, fontVariantNumeric: 'tabular-nums', minWidth: 0 }}
        >
          <Box component="strong" sx={{ fontWeight: 700 }}>
            {selection.length}
          </Box>{' '}
          selected
        </Typography>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => {
            onSave(selection);
            onClose();
          }}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function RailItem({
  label,
  count,
  chosen = 0,
  selected,
  onClick,
}: {
  label: string;
  count: number;
  chosen?: number;
  selected: boolean;
  onClick: () => void;
}) {
  const theme = useTheme();
  const primary = theme.palette.primary.main;
  return (
    <ButtonBase
      onClick={onClick}
      aria-current={selected ? 'true' : undefined}
      sx={{
        alignItems: 'flex-start',
        bgcolor: selected ? alpha(primary, 0.12) : 'transparent',
        borderRadius: 1,
        display: 'flex',
        gap: 1,
        justifyContent: 'flex-start',
        px: 1.5,
        py: 0.875,
        textAlign: 'left',
        width: '100%',
        '&:hover': { bgcolor: alpha(primary, selected ? 0.16 : 0.06) },
        '&.Mui-focusVisible': {
          outline: `2px solid ${primary}`,
          outlineOffset: -2,
        },
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          variant="body2"
          noWrap
          sx={{
            color: count || selected ? 'text.primary' : 'text.disabled',
            fontWeight: selected ? 700 : 500,
          }}
        >
          {label}
        </Typography>
        {chosen > 0 && (
          <Typography
            variant="caption"
            component="div"
            sx={{ color: 'primary.main', fontWeight: 600 }}
          >
            {chosen} selected
          </Typography>
        )}
      </Box>
      <Typography
        variant="caption"
        sx={{
          color: count ? 'text.secondary' : 'text.disabled',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: '20px',
        }}
      >
        {count}
      </Typography>
    </ButtonBase>
  );
}

function Count({ children }: { children: number }) {
  return (
    <Box
      component="span"
      sx={{
        color: 'text.secondary',
        fontVariantNumeric: 'tabular-nums',
        fontWeight: 600,
        ml: 0.75,
      }}
    >
      {children}
    </Box>
  );
}

function EmptyState({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Box sx={{ px: 1, py: 6, textAlign: 'center' }}>
      <Typography color="text.secondary">{children}</Typography>
      {action && <Box sx={{ mt: 1.5 }}>{action}</Box>}
    </Box>
  );
}

function ToolRow({
  tool,
  terms,
  checked,
  onToggle,
}: {
  tool: ToolEntry;
  terms: string[];
  checked: boolean;
  onToggle: () => void;
}) {
  const theme = useTheme();
  const primary = theme.palette.primary.main;
  const id = `allowed-tool-${tool.declaration}`;
  return (
    <Box
      component="li"
      sx={{ borderBottom: 1, borderColor: 'divider', py: 0.25 }}
    >
      <Box
        component="label"
        htmlFor={id}
        sx={{
          alignItems: 'flex-start',
          bgcolor: checked ? alpha(primary, 0.1) : 'transparent',
          borderRadius: 1,
          cursor: 'pointer',
          display: 'flex',
          gap: 0.5,
          minHeight: 48,
          pr: 1.5,
          py: 0.5,
          transition: theme.transitions.create('background-color', {
            duration: 120,
          }),
          '&:hover': { bgcolor: alpha(primary, checked ? 0.14 : 0.06) },
        }}
      >
        <Checkbox
          id={id}
          checked={checked}
          onChange={onToggle}
          size="small"
          // Named explicitly: the visible label is split by highlights and
          // line-break opportunities that some name computations space out.
          slotProps={{
            input: {
              'aria-label': `${tool.label} ${tool.declaration}${
                tool.enabled ? '' : ' (disabled)'
              }`,
            },
          }}
          sx={{ mt: 0.25 }}
        />
        <Box sx={{ flex: 1, minWidth: 0, pt: 0.75 }}>
          <Box sx={{ alignItems: 'baseline', display: 'flex', gap: 1 }}>
            <Typography
              variant="body2"
              sx={{
                color: checked ? 'text.primary' : 'text.secondary',
                fontWeight: checked ? 700 : 500,
                overflowWrap: 'anywhere',
              }}
            >
              <Highlight text={tool.label} terms={terms} wrapAtUnderscores />
            </Typography>
            {!tool.enabled && (
              <Typography
                component="span"
                variant="caption"
                sx={{ color: 'warning.main', flexShrink: 0, fontWeight: 600 }}
              >
                Disabled
              </Typography>
            )}
          </Box>
          {/* A declaration is one unbroken token, so it must break inside a
              word or it pushes the dialog sideways. */}
          <Typography
            variant="caption"
            component="div"
            sx={{
              color: checked ? 'primary.main' : 'text.secondary',
              fontFamily: fontFamilyMono,
              overflowWrap: 'anywhere',
            }}
          >
            <Highlight
              text={tool.declaration}
              terms={terms}
              wrapAtUnderscores
            />
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}
