import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Card,
  CardContent,
  TextField,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import PlayArrow from '@mui/icons-material/PlayArrow';
import CypherGraph from 'src/components/reports/CypherGraph';
import QueryConsoleSchemaPanel from 'src/components/QueryConsoleSchemaPanel';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  useFetchHistoryItem,
  QueryHistoryItem,
} from 'src/hooks/useQueryHistory';
import { pageContentSx } from 'src/theme/layout';

/** Exactly one thing is running: a typed query, or a stored history entry. */
type ConsoleRun =
  | { kind: 'query'; query: string; key: number }
  | { kind: 'history'; historyId: string; key: number };

const QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY =
  'seizu:query-console:schema-panel-open';

export default function QueryConsole() {
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const navigate = useNavigate();
  const location = useLocation();
  const fetchHistoryItem = useFetchHistoryItem();
  const [queryText, setQueryText] = useState('');
  // What this page is showing, and nothing else decides it. `location` is
  // deliberately absent from this: deriving the run from the address bar means
  // re-deriving it while our own navigate() is still settling, and a completed
  // query that publishes its URL then reads back as a request to run something,
  // which publishes again. The console re-ran one query 222 times that way.
  const [run, setRun] = useState<ConsoleRun | null>(null);
  // Every `?h=` this page put in the address bar, and the last URL the restore
  // below has already acted on. Both are refs mutated only from callbacks and
  // never cleared on read, so a double-invoked effect or render repeats no work
  // -- unlike the `justPushedRef` this replaces, which cleared its flag as it
  // read it and so was defeated by StrictMode's second pass.
  const ownHistoryIds = useRef<Set<string>>(new Set());
  const handledSearch = useRef<string | null>(null);

  const [schemaPanelOpen, setSchemaPanelOpen] = useState(() => {
    if (typeof window === 'undefined') return true;
    const storedValue = window.localStorage.getItem(
      QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY,
    );
    return storedValue === null ? true : storedValue === 'true';
  });
  const [historyRefreshTrigger, setHistoryRefreshTrigger] = useState(0);
  const [queryHeight, setQueryHeight] = useState(220);
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);

  const handleQueryComplete = useCallback(
    (historyId: string | null) => {
      // Null for a re-executed history entry, which creates no new record and
      // so has nothing to publish.
      if (!historyId) return;
      setHistoryRefreshTrigger((n) => n + 1);
      // Claimed before navigating, so the restore below never treats this
      // page's own URL as somewhere the user asked to go.
      ownHistoryIds.current.add(historyId);
      handledSearch.current = `?h=${historyId}`;
      navigate(`?h=${historyId}`);
    },
    [navigate],
  );

  const submittedQuery = run?.kind === 'query' ? run.query : undefined;
  const submittedHistoryId =
    run?.kind === 'history' ? run.historyId : undefined;
  const runKey = run?.key ?? 0;

  // The address bar is an *input* only when it names a history entry this page
  // did not publish: a back/forward, or a link opened cold. That is a change in
  // an external system (the history stack) rather than a value this render
  // could derive, so it is read here and acted on once per URL -- the narrow
  // case UI-003 leaves to an effect.
  useEffect(() => {
    if (handledSearch.current === location.search) return undefined;
    handledSearch.current = location.search;

    const historyId = new URLSearchParams(location.search).get('h');
    if (!historyId || ownHistoryIds.current.has(historyId)) return undefined;
    ownHistoryIds.current.add(historyId);

    let cancelled = false;
    // The one place this page writes run state from an effect, because the
    // history stack is an external system rather than something this render
    // can derive -- the carve-out UI-003 names, recorded as UI-005.
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- see UI-005
    setRun((previous) => ({
      kind: 'history',
      historyId,
      key: (previous?.key ?? 0) + 1,
    }));
    void fetchHistoryItem(historyId).then((item) => {
      if (cancelled || !item) return;
      setQueryText(item.query);
    });
    return () => {
      cancelled = true;
    };
    // `fetchHistoryItem` resolves null until there is a token, so the editor
    // text needs no gate of its own here.
  }, [location.search, fetchHistoryItem]);

  const queryTextRef = useRef(queryText);
  queryTextRef.current = queryText;

  const handleRun = useCallback(() => {
    const trimmed = queryTextRef.current.trim();
    if (!trimmed) return;
    setRun((previous) => ({
      kind: 'query',
      query: trimmed,
      key: (previous?.key ?? 0) + 1,
    }));
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        handleRun();
      }
    },
    [handleRun],
  );

  /** Insert a query from the schema browser and run it immediately. */
  const handleQuerySelect = useCallback((query: string) => {
    setQueryText(query);
    setRun((previous) => ({
      kind: 'query',
      query,
      key: (previous?.key ?? 0) + 1,
    }));
  }, []);

  /** Load a query from history into the editor and re-execute by history ID. */
  const handleHistorySelect = useCallback(
    (item: QueryHistoryItem) => {
      setQueryText(item.query);
      ownHistoryIds.current.add(item.history_id);
      handledSearch.current = `?h=${item.history_id}`;
      setRun((previous) => ({
        kind: 'history',
        historyId: item.history_id,
        key: (previous?.key ?? 0) + 1,
      }));
      navigate(`?h=${item.history_id}`);
    },
    [navigate],
  );

  const handleSchemaPanelToggle = useCallback((tab?: 'schema' | 'history') => {
    if (tab) {
      setSchemaPanelOpen(true);
      return;
    }
    setSchemaPanelOpen((value) => !value);
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY,
      String(schemaPanelOpen),
    );
  }, [schemaPanelOpen]);

  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragStartY.current = e.clientY;
      dragStartHeight.current = queryHeight;
      document.body.style.cursor = 'ns-resize';
      document.body.style.userSelect = 'none';

      const handleMouseMove = (ev: MouseEvent) => {
        const delta = dragStartY.current - ev.clientY;
        setQueryHeight(
          Math.max(100, Math.min(600, dragStartHeight.current + delta)),
        );
      };

      const handleMouseUp = () => {
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };

      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    },
    [queryHeight],
  );

  if (permissionsLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (!hasPermission('query:execute')) {
    return (
      <Box sx={pageContentSx}>
        <Typography>You do not have access to the query console.</Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{ display: 'flex', height: 'calc(100vh - 64px)', overflow: 'hidden' }}
    >
      {/* Side panel (schema / history) */}
      <QueryConsoleSchemaPanel
        open={schemaPanelOpen}
        onToggle={handleSchemaPanelToggle}
        onQuerySelect={handleQuerySelect}
        onHistorySelect={handleHistorySelect}
        historyRefreshTrigger={historyRefreshTrigger}
      />

      {/* Main content */}
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          ...pageContentSx,
          boxSizing: 'border-box',
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        {/* Graph panel — detail panel open by default in the console */}
        <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          {submittedQuery || submittedHistoryId ? (
            <CypherGraph
              cypher={submittedHistoryId ? undefined : submittedQuery}
              queryHistoryId={submittedHistoryId}
              defaultDetailOpen
              fillHeight
              refreshKey={runKey}
              onQueryComplete={handleQueryComplete}
            />
          ) : (
            <Card
              sx={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <CardContent>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  align="center"
                >
                  Run a query below to visualize the graph.
                </Typography>
              </CardContent>
            </Card>
          )}
        </Box>

        {/* Resize handle */}
        <Box
          onMouseDown={handleDragStart}
          sx={{
            height: 8,
            flexShrink: 0,
            cursor: 'ns-resize',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            my: 0.5,
            '&::after': {
              content: '""',
              display: 'block',
              width: 48,
              height: 4,
              borderRadius: 2,
              bgcolor: 'divider',
              transition: 'background-color 0.15s',
            },
            '&:hover::after': { bgcolor: 'primary.main' },
          }}
        />

        {/* Query editor */}
        <Box sx={{ height: queryHeight, flexShrink: 0 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent
              sx={{
                height: '100%',
                boxSizing: 'border-box',
                display: 'flex',
                flexDirection: 'column',
                '&:last-child': { pb: 2 },
              }}
            >
              <TextField
                multiline
                fullWidth
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Enter a Cypher query... (Ctrl+Enter to run)"
                variant="outlined"
                sx={{
                  flex: 1,
                  minHeight: 0,
                  '& .MuiInputBase-root': {
                    height: '100%',
                    alignItems: 'flex-start',
                  },
                  '& .MuiInputBase-input': {
                    height: '100% !important',
                    overflow: 'auto !important',
                    boxSizing: 'border-box',
                  },
                }}
                slotProps={{
                  htmlInput: {
                    style: { fontFamily: 'monospace', fontSize: 13 },
                  },
                }}
              />
              <Box
                sx={{
                  mt: 1,
                  display: 'flex',
                  justifyContent: 'flex-end',
                  flexShrink: 0,
                }}
              >
                <Button
                  variant="contained"
                  startIcon={<PlayArrow />}
                  onClick={handleRun}
                  disabled={!queryText.trim()}
                >
                  Run
                </Button>
              </Box>
            </CardContent>
          </Card>
        </Box>
      </Box>
    </Box>
  );
}
