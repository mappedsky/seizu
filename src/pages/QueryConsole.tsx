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
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';
import { pageContentSx } from 'src/theme/layout';

const QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY =
  'seizu:query-console:schema-panel-open';

export default function QueryConsole() {
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const navigate = useNavigate();
  const location = useLocation();
  const fetchHistoryItem = useFetchHistoryItem();
  const { authReady } = useAuthHeaders();
  const [queryText, setQueryText] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState<string | undefined>(
    undefined,
  );
  const [submittedHistoryId, setSubmittedHistoryId] = useState<
    string | undefined
  >(undefined);
  const [runKey, setRunKey] = useState(0);
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

  // justPushedRef: the history ID we most recently pushed to the URL ourselves,
  // so we can skip re-running when our own navigate() triggers a location change.
  const justPushedRef = useRef<string | null>(null);

  const handleQueryComplete = useCallback(
    (historyId: string | null) => {
      if (historyId) {
        setHistoryRefreshTrigger((n) => n + 1);
        justPushedRef.current = historyId;
        navigate(`?h=${historyId}`);
      }
    },
    [navigate],
  );

  // Restore and re-run query when URL changes via browser back/forward.
  useEffect(() => {
    const h = new URLSearchParams(location.search).get('h');
    if (!h) return;
    if (!authReady) return;
    if (justPushedRef.current === h) {
      justPushedRef.current = null;
      return;
    }
    setSubmittedHistoryId(h);
    setSubmittedQuery(undefined);
    setRunKey((k) => k + 1);
    let cancelled = false;
    fetchHistoryItem(h).then((item) => {
      if (cancelled || !item) return;
      setQueryText(item.query);
    });
    return () => {
      cancelled = true;
    };
  }, [authReady, location.search, fetchHistoryItem]);

  const queryTextRef = useRef(queryText);
  queryTextRef.current = queryText;

  const handleRun = useCallback(() => {
    const trimmed = queryTextRef.current.trim();
    if (!trimmed) return;
    setSubmittedHistoryId(undefined);
    setSubmittedQuery(trimmed);
    setRunKey((k) => k + 1);
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
    setSubmittedQuery(query);
    setSubmittedHistoryId(undefined);
    setRunKey((k) => k + 1);
  }, []);

  /** Load a query from history into the editor and re-execute by history ID. */
  const handleHistorySelect = useCallback(
    (item: QueryHistoryItem) => {
      setQueryText(item.query);
      setSubmittedHistoryId(item.history_id);
      setSubmittedQuery(undefined);
      setRunKey((k) => k + 1);
      justPushedRef.current = item.history_id;
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
