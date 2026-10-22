import { memo, useEffect, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  IconButton,
  InputAdornment,
  List,
  ListItem,
  ListItemButton,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import AccountTree from '@mui/icons-material/AccountTree';
import ChevronLeft from '@mui/icons-material/ChevronLeft';
import ExpandMore from '@mui/icons-material/ExpandMore';
import History from '@mui/icons-material/History';
import SearchIcon from '@mui/icons-material/Search';
import { useGraphSchema, type GraphIndex } from 'src/hooks/useGraphSchema';
import { colorForGroup } from 'src/components/reports/CypherGraph';
import { chartPalette } from 'src/theme/brand';
import QueryConsoleHistoryPanel from 'src/components/QueryConsoleHistoryPanel';
import { QueryHistoryItem } from 'src/hooks/useQueryHistory';
import SidePanelFrame from 'src/components/SidePanelFrame';
import { fontFamilyMono, fontSizeMin } from 'src/theme/typography';

/** Render an index target like "(:CVE) {id}" or "[:DEPENDS_ON] {version}". */
function indexTarget(index: GraphIndex): string {
  const names = index.labels_or_types.join(':');
  const scope = names
    ? index.entity_type === 'RELATIONSHIP'
      ? `[:${names}]`
      : `(:${names})`
    : '';
  const properties = index.properties.length
    ? ` {${index.properties.join(', ')}}`
    : '';
  return `${scope}${properties}`.trim();
}

/**
 * The schema narrowed to what matches a filter, case-insensitively: labels,
 * relationship types and property keys by name, and indexes by name, target
 * or type. An empty filter keeps everything.
 */
export function filterSchema(
  schema: {
    labels: string[];
    rels: string[];
    props: string[];
    indexes: GraphIndex[];
  },
  filter: string,
): {
  labels: string[];
  rels: string[];
  props: string[];
  indexes: GraphIndex[];
} {
  const query = filter.trim().toLowerCase();
  if (!query) return schema;
  const matches = (value: string) => value.toLowerCase().includes(query);
  return {
    labels: schema.labels.filter(matches),
    rels: schema.rels.filter(matches),
    props: schema.props.filter(matches),
    indexes: schema.indexes.filter(
      (index) =>
        matches(index.name) ||
        matches(indexTarget(index)) ||
        matches(index.type),
    ),
  };
}

/** A section's count: the total, or how many of it match while filtering. */
function sectionCount(shown: number, total: number, filtering: boolean) {
  if (total === 0) return '';
  return filtering ? ` (${shown} of ${total})` : ` (${total})`;
}

type ActiveTab = 'schema' | 'history';

interface QueryConsoleSchemaProps {
  open: boolean;
  /** Float over the console instead of beside it (see `useSidePanel`). */
  overlay?: boolean;
  onToggle: (tab?: ActiveTab) => void;
  onQuerySelect: (query: string) => void;
  onHistorySelect: (item: QueryHistoryItem) => void;
  historyRefreshTrigger?: number;
}

function QueryConsoleSchemaPanel({
  open,
  overlay = false,
  onToggle,
  onQuerySelect,
  onHistorySelect,
  historyRefreshTrigger,
}: QueryConsoleSchemaProps) {
  const theme = useTheme();
  const graphPalette =
    theme.palette.mode === 'dark' ? chartPalette.dark : chartPalette.light;
  const [activeTab, setActiveTab] = useState<ActiveTab>('schema');

  const { schema, fetchSchema } = useGraphSchema();

  useEffect(() => {
    fetchSchema();
  }, [fetchSchema]);

  const all = {
    labels: schema?.labels ?? [],
    rels: schema?.relationship_types ?? [],
    props: schema?.property_keys ?? [],
    indexes: schema?.indexes ?? [],
  };
  const [filter, setFilter] = useState('');
  const filtering = filter.trim() !== '';
  const { labels, rels, props, indexes } = filterSchema(all, filter);
  const nothingMatches =
    filtering &&
    labels.length + rels.length + props.length + indexes.length === 0;
  // While filtering, a section with no matches is left out.
  const showSection = (shown: number) => !filtering || shown > 0;

  /** Open or switch to a tab. If already open on the same tab, collapse. */
  const handleTabClick = (tab: ActiveTab) => {
    if (open && activeTab === tab) {
      onToggle();
    } else {
      setActiveTab(tab);
      if (!open) onToggle(tab);
    }
  };

  return (
    <SidePanelFrame open={open} overlay={overlay} onClose={() => onToggle()}>
      {/* Header / toggle */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: open ? 'space-between' : 'center',
          px: open ? 1.5 : 0,
          minHeight: 40,
          borderBottom: 1,
          borderColor: 'divider',
          flexShrink: 0,
        }}
      >
        {open ? (
          <>
            <Typography
              variant="caption"
              sx={{
                fontWeight: 700,
                letterSpacing: 0.8,
                color: 'text.secondary',
              }}
            >
              {activeTab === 'schema' ? 'DATABASE' : 'HISTORY'}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center' }}>
              <Tooltip title="Database schema" placement="right">
                <IconButton
                  size="small"
                  onClick={() => handleTabClick('schema')}
                  color={activeTab === 'schema' ? 'primary' : 'default'}
                >
                  <AccountTree fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="Query history" placement="right">
                <IconButton
                  size="small"
                  onClick={() => handleTabClick('history')}
                  color={activeTab === 'history' ? 'primary' : 'default'}
                >
                  <History fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="Collapse" placement="right">
                <IconButton size="small" onClick={() => onToggle()}>
                  <ChevronLeft fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          </>
        ) : (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 0.5,
            }}
          >
            <Tooltip title="Database schema" placement="right">
              <IconButton size="small" onClick={() => handleTabClick('schema')}>
                <AccountTree fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Query history" placement="right">
              <IconButton
                size="small"
                onClick={() => handleTabClick('history')}
              >
                <History fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        )}
      </Box>

      {open && activeTab === 'schema' && (
        <Box sx={{ flexShrink: 0, px: 1, pt: 1, pb: 0.5 }}>
          <TextField
            fullWidth
            size="small"
            placeholder="Filter schema"
            value={filter}
            // The rows' size, so the field reads as part of the list.
            sx={{ '& input': { typography: 'body2' } }}
            onChange={(event) => setFilter(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && filter) {
                event.stopPropagation();
                setFilter('');
              }
            }}
            slotProps={{
              htmlInput: { 'aria-label': 'Filter schema' },
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              },
            }}
          />
        </Box>
      )}

      {/* Scrollable content */}
      {open && (
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            overflow: activeTab === 'history' ? 'hidden' : 'auto',
          }}
        >
          {activeTab === 'schema' ? (
            <>
              {nothingMatches ? (
                <Box sx={{ color: 'text.secondary', px: 1.5, py: 1 }}>
                  <Typography variant="caption">
                    Nothing in the schema matches.
                  </Typography>
                </Box>
              ) : null}
              {/* ── Nodes ─────────────────────────────────────────── */}
              {showSection(labels.length) && (
                <Accordion
                  defaultExpanded
                  disableGutters
                  elevation={0}
                  square
                  sx={{ '&:before': { display: 'none' } }}
                >
                  <AccordionSummary
                    expandIcon={<ExpandMore fontSize="small" />}
                    sx={{
                      minHeight: 36,
                      px: 1.5,
                      '& .MuiAccordionSummary-content': { my: 0.5 },
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{ fontWeight: 700, letterSpacing: 0.5 }}
                    >
                      NODES
                      {sectionCount(
                        labels.length,
                        all.labels.length,
                        filtering,
                      )}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <List dense disablePadding>
                      {labels.map((label) => (
                        <ListItemButton
                          key={label}
                          onClick={() =>
                            onQuerySelect(
                              `MATCH path = (n:\`${label}\`)-[r]-(m) RETURN path LIMIT 25`,
                            )
                          }
                          sx={{ py: 0.5, px: 1.5 }}
                        >
                          {/* Styled like graph nodes: filled circle */}
                          <Box
                            sx={{
                              width: 14,
                              height: 14,
                              borderRadius: '50%',
                              bgcolor: colorForGroup(label, graphPalette),
                              flexShrink: 0,
                              mr: 1,
                              border: `2px solid ${theme.palette.background.paper}`,
                              boxShadow: `0 0 0 1px ${colorForGroup(label, graphPalette)}`,
                            }}
                          />
                          <Typography variant="body2" noWrap>
                            {label}
                          </Typography>
                        </ListItemButton>
                      ))}
                    </List>
                  </AccordionDetails>
                </Accordion>
              )}

              {/* ── Relationships ──────────────────────────────────── */}
              {showSection(rels.length) && (
                <Accordion
                  defaultExpanded
                  disableGutters
                  elevation={0}
                  square
                  sx={{
                    '&:before': { display: 'none' },
                    borderTop: 1,
                    borderColor: 'divider',
                  }}
                >
                  <AccordionSummary
                    expandIcon={<ExpandMore fontSize="small" />}
                    sx={{
                      minHeight: 36,
                      px: 1.5,
                      '& .MuiAccordionSummary-content': { my: 0.5 },
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{ fontWeight: 700, letterSpacing: 0.5 }}
                    >
                      RELATIONSHIPS
                      {sectionCount(rels.length, all.rels.length, filtering)}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <List dense disablePadding>
                      {rels.map((type) => (
                        <ListItemButton
                          key={type}
                          onClick={() =>
                            onQuerySelect(
                              `MATCH path = (a)-[r:\`${type}\`]->(b) RETURN path LIMIT 25`,
                            )
                          }
                          sx={{ py: 0.75, px: 1.5 }}
                        >
                          {/* ──[TYPE]──► */}
                          <Box
                            sx={{
                              display: 'flex',
                              alignItems: 'center',
                              minWidth: 0,
                            }}
                          >
                            <Box
                              sx={{
                                width: 10,
                                height: 1.5,
                                bgcolor: 'text.disabled',
                                flexShrink: 0,
                              }}
                            />
                            <Box
                              sx={{
                                border: 1,
                                borderColor: 'text.secondary',
                                borderRadius: 0.5,
                                px: 0.75,
                                lineHeight: '18px',
                                fontSize: fontSizeMin,
                                fontFamily: fontFamilyMono,
                                color: 'text.secondary',
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                maxWidth: 160,
                                flexShrink: 1,
                              }}
                            >
                              {type}
                            </Box>
                            <Box
                              sx={{
                                width: 10,
                                height: 1.5,
                                bgcolor: 'text.disabled',
                                flexShrink: 0,
                              }}
                            />
                            {/* arrowhead */}
                            <Box
                              sx={{
                                width: 0,
                                height: 0,
                                borderTop: '4px solid transparent',
                                borderBottom: '4px solid transparent',
                                borderLeft: `5px solid ${theme.palette.text.disabled}`,
                                flexShrink: 0,
                              }}
                            />
                          </Box>
                        </ListItemButton>
                      ))}
                    </List>
                  </AccordionDetails>
                </Accordion>
              )}

              {/* ── Property keys ──────────────────────────────────── */}
              {showSection(props.length) && (
                <Accordion
                  defaultExpanded
                  disableGutters
                  elevation={0}
                  square
                  sx={{
                    '&:before': { display: 'none' },
                    borderTop: 1,
                    borderColor: 'divider',
                  }}
                >
                  <AccordionSummary
                    expandIcon={<ExpandMore fontSize="small" />}
                    sx={{
                      minHeight: 36,
                      px: 1.5,
                      '& .MuiAccordionSummary-content': { my: 0.5 },
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{ fontWeight: 700, letterSpacing: 0.5 }}
                    >
                      PROPERTY KEYS
                      {sectionCount(props.length, all.props.length, filtering)}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <List dense disablePadding>
                      {props.map((key) => (
                        <ListItemButton
                          key={key}
                          onClick={() =>
                            onQuerySelect(
                              `MATCH (n) WHERE n.\`${key}\` IS NOT NULL RETURN n LIMIT 25`,
                            )
                          }
                          sx={{ py: 0.5, px: 1.5 }}
                        >
                          <Typography
                            variant="body2"
                            noWrap
                            sx={{
                              fontFamily: fontFamilyMono,
                              fontSize: 12,
                              color: 'text.secondary',
                            }}
                          >
                            # {key}
                          </Typography>
                        </ListItemButton>
                      ))}
                    </List>
                  </AccordionDetails>
                </Accordion>
              )}

              {/* ── Indexes ────────────────────────────────────────── */}
              {showSection(indexes.length) && (
                <Accordion
                  defaultExpanded
                  disableGutters
                  elevation={0}
                  square
                  sx={{
                    '&:before': { display: 'none' },
                    borderTop: 1,
                    borderColor: 'divider',
                  }}
                >
                  <AccordionSummary
                    expandIcon={<ExpandMore fontSize="small" />}
                    sx={{
                      minHeight: 36,
                      px: 1.5,
                      '& .MuiAccordionSummary-content': { my: 0.5 },
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{ fontWeight: 700, letterSpacing: 0.5 }}
                    >
                      INDEXES
                      {sectionCount(
                        indexes.length,
                        all.indexes.length,
                        filtering,
                      )}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <List dense disablePadding>
                      {indexes.map((index) => (
                        <ListItem
                          key={index.name}
                          sx={{ display: 'block', py: 0.5, px: 1.5 }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              fontFamily: fontFamilyMono,
                              fontSize: 12,
                              color: 'text.secondary',
                              whiteSpace: 'normal',
                              overflowWrap: 'anywhere',
                            }}
                          >
                            {indexTarget(index) || index.name}
                          </Typography>
                          <Typography
                            variant="caption"
                            sx={{
                              display: 'block',
                              color: 'text.secondary',
                              whiteSpace: 'normal',
                              overflowWrap: 'anywhere',
                            }}
                          >
                            {index.type}
                            {index.state !== 'ONLINE'
                              ? ` · ${index.state}`
                              : ''}{' '}
                            · {index.name}
                          </Typography>
                        </ListItem>
                      ))}
                    </List>
                  </AccordionDetails>
                </Accordion>
              )}
            </>
          ) : (
            <QueryConsoleHistoryPanel
              onQuerySelect={onHistorySelect}
              refreshTrigger={historyRefreshTrigger}
            />
          )}
        </Box>
      )}
    </SidePanelFrame>
  );
}

export default memo(QueryConsoleSchemaPanel);
