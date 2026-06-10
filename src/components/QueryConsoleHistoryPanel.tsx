import { useEffect, useState } from 'react';
import {
  Box,
  List,
  ListItemButton,
  Pagination,
  Tooltip,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ErrorIcon from '@mui/icons-material/Error';
import { useQueryHistory, QueryHistoryItem } from 'src/hooks/useQueryHistory';

const PER_PAGE = 20;

interface QueryConsoleHistoryPanelProps {
  onQuerySelect: (item: QueryHistoryItem) => void;
  refreshTrigger?: number;
}

export default function QueryConsoleHistoryPanel({
  onQuerySelect,
  refreshTrigger,
}: QueryConsoleHistoryPanelProps) {
  const { loading, error, data, authReady, fetchHistory } = useQueryHistory();
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (!authReady) return;
    fetchHistory(page, PER_PAGE);
  }, [authReady, fetchHistory, page]);

  // Re-fetch from page 1 whenever a new query completes.
  useEffect(() => {
    if (!refreshTrigger) return;
    if (!authReady) return;
    if (page === 1) {
      fetchHistory(1, PER_PAGE);
    } else {
      setPage(1);
    }
  }, [authReady, fetchHistory, page, refreshTrigger]);

  const totalPages = data ? Math.ceil(data.total / PER_PAGE) : 0;

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', pt: 3 }}>
        <ConstellationSpinner size={24} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box
        sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, pt: 2 }}
      >
        <ErrorIcon fontSize="small" color="error" />
        <Typography variant="caption" color="error">
          Failed to load history
        </Typography>
      </Box>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return (
      <Box sx={{ px: 1.5, pt: 2 }}>
        <Typography variant="caption" color="text.secondary">
          No history yet. Run a query to get started.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
      }}
    >
      <List dense disablePadding sx={{ flex: 1, overflow: 'auto' }}>
        {items.map((item) => (
          <Tooltip
            key={item.history_id}
            title={item.query}
            placement="right"
            slotProps={{
              tooltip: { sx: { fontFamily: 'monospace', fontSize: 11 } },
            }}
          >
            <ListItemButton
              onClick={() => onQuerySelect(item)}
              sx={{
                py: 0.75,
                px: 1.5,
                flexDirection: 'column',
                alignItems: 'flex-start',
              }}
            >
              <Typography
                variant="body2"
                noWrap
                sx={{ fontFamily: 'monospace', fontSize: 11, width: '100%' }}
              >
                {item.query}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: 10 }}
              >
                {new Date(item.executed_at).toLocaleString()}
              </Typography>
            </ListItemButton>
          </Tooltip>
        ))}
      </List>

      {totalPages > 1 && (
        <Box
          sx={{
            px: 1,
            py: 0.5,
            borderTop: 1,
            borderColor: 'divider',
            flexShrink: 0,
          }}
        >
          <Pagination
            count={totalPages}
            page={page}
            onChange={(_e, v) => setPage(v)}
            size="small"
            siblingCount={0}
            boundaryCount={1}
          />
        </Box>
      )}
    </Box>
  );
}
