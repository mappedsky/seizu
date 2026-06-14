import { ChangeEvent, useCallback, useEffect, useRef, useState } from 'react';
import Error from '@mui/icons-material/Error';
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogActions,
  IconButton,
  Paper,
  Popover,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  DataGrid,
  GridColDef,
  GridDensity,
  GridPreferencePanelsValue,
  GridRowsProp,
  GridToolbarContainer,
  useGridApiContext,
} from '@mui/x-data-grid';
import Info from '@mui/icons-material/Info';
import Fullscreen from '@mui/icons-material/Fullscreen';
import CloseFullscreen from '@mui/icons-material/CloseFullscreen';
import Search from '@mui/icons-material/Search';
import ViewColumn from '@mui/icons-material/ViewColumn';
import FilterList from '@mui/icons-material/FilterList';
import DensitySmall from '@mui/icons-material/DensitySmall';
import Download from '@mui/icons-material/Download';

import { useLazyCypherQuery, QueryRecord } from 'src/hooks/useCypherQuery';
import { TablePanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import { getCspNonce } from 'src/cspNonce';

/**
 * Format any value for display in a table cell.
 *
 * Understands the Neo4j serialization shapes produced by the backend:
 *  - Node:         {id, labels, properties}  → "(Label) name"
 *  - Relationship: {id, type, start_node_id} → "[TYPE]"
 *  - Path:         {nodes, relationships}    → node labels joined with " → "
 *  - Array:        each element formatted recursively, joined with ", "
 *  - Plain object: "key: value" pairs joined with ", "
 *  - Primitive:    String(value)
 */
export function formatValue(val: unknown): string {
  if (val === null || val === undefined) return '';
  if (typeof val !== 'object') return String(val);
  if (Array.isArray(val)) return val.map(formatValue).join(', ');

  const obj = val as Record<string, unknown>;

  // Neo4j node: {id, labels: [...], properties: {...}}
  if (
    Array.isArray(obj.labels) &&
    typeof obj.properties === 'object' &&
    obj.properties !== null
  ) {
    const label = (obj.labels as string[])[0] ?? '';
    const props = obj.properties as Record<string, unknown>;
    const name = props['name'] ?? props['id'] ?? obj['id'];
    return label ? `(${label}) ${String(name)}` : String(name);
  }

  // Neo4j relationship: {type, start_node_id, end_node_id, ...}
  if (typeof obj['type'] === 'string' && 'start_node_id' in obj) {
    return `[${obj['type']}]`;
  }

  // Path: {nodes: [...], relationships: [...]}
  if (Array.isArray(obj['nodes']) && Array.isArray(obj['relationships'])) {
    return (obj['nodes'] as unknown[]).map(formatValue).join(' → ');
  }

  // Generic object: "key: value" pairs
  return Object.entries(obj)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}: ${formatValue(v)}`)
    .join(', ');
}

/**
 * Flatten a query record into a plain key→value map suitable for table display.
 *
 * Handles three shapes:
 *  - Multiple columns: `RETURN n.name AS name, n.org AS org`  → use record as-is
 *  - Single key, plain object: `RETURN {name: n.name} AS row` → unwrap the value
 *  - Single key, Neo4j node: `RETURN n` or `RETURN n AS x`   → unwrap `.properties`
 */
export function flattenRecord(record: QueryRecord): QueryRecord {
  const keys = Object.keys(record);
  if (keys.length === 1) {
    const val = record[keys[0]];
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      const obj = val as QueryRecord;
      if (
        obj.properties &&
        typeof obj.properties === 'object' &&
        !Array.isArray(obj.properties)
      ) {
        return obj.properties as QueryRecord;
      }
      return obj;
    }
  }
  return record;
}
import CypherDetails from 'src/components/reports/CypherDetails';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';

// Approximate height consumed by panel chrome outside the Data Grid. The grid
// owns its toolbar, column header, rows, and pagination inside this height.
const TABLE_CHROME_HEIGHT = 0;
const CAPTION_HEIGHT = 28;
const MIN_TABLE_BODY_HEIGHT = 120;

// Override MUI Toolbar / Data Grid / Pagination defaults to a tighter density so
// the table body has more vertical room inside the panel cell.
const fillSx = {
  height: '100%',
  display: 'flex',
  flexDirection: 'column' as const,
  minHeight: 0,
  '& .MuiToolbar-root': {
    minHeight: 44,
    paddingLeft: 1,
    paddingRight: 1,
  },
  '& .MuiTableCell-head': {
    paddingTop: 0.5,
    paddingBottom: 0.5,
    lineHeight: 1.2,
  },
  '& .MuiTablePagination-root': {
    minHeight: 40,
  },
  '& .MuiTablePagination-toolbar': {
    minHeight: 40,
    paddingLeft: 1,
    paddingRight: 1,
  },
  '& .MuiIconButton-root': {
    padding: 0.5,
  },
  '& .MuiDataGrid-root': {
    border: 0,
  },
  '& .MuiDataGrid-columnHeader': {
    minHeight: '40px !important',
    maxHeight: '40px !important',
  },
  '& .MuiDataGrid-cell': {
    alignItems: 'center',
    display: 'flex',
  },
  '& .MuiDataGrid-cellContent': {
    display: 'flex',
    alignItems: 'center',
  },
};

interface CypherTableProps {
  cypher?: string;
  params?: Record<string, unknown>;
  columns?: Array<{ name: string; label: string }>;
  caption?: string;
  needInputs?: string[];
  details?: Record<string, unknown>;
  height?: string;
  reportQueryToken?: string;
  refreshKey?: number;
  onTokenExpired?: () => void;
  /** When provided, skip fetching and render this data directly. Used by CypherGraph to share its already-fetched records across tab switches. */
  preloadedRecords?: QueryRecord[];
}

interface GridRow extends Record<string, unknown> {
  __rowId: string;
}

function makeGridColumns(
  columns: Array<{ name: string; label: string }>,
): GridColDef[] {
  return columns.map((column) => ({
    field: column.name,
    headerName: column.label,
    minWidth: 160,
    flex: 1,
    renderCell: (params) => (
      <Typography
        variant="body2"
        title={params.formattedValue ?? ''}
        sx={{
          display: 'flex',
          alignItems: 'center',
          height: '100%',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {params.formattedValue}
      </Typography>
    ),
  }));
}

function EmptyOverlay({ message }: { message: string }) {
  return (
    <Box
      sx={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        p: 2,
      }}
    >
      <Typography variant="body2" color="text.secondary">
        {message}
      </Typography>
    </Box>
  );
}

interface GridActionToolbarProps {
  caption?: string;
  warnings: string[];
  queryErrors: string[];
  details?: Record<string, unknown>;
  expandOpen: boolean;
  density: GridDensity;
  onDensityChange: (density: GridDensity) => void;
  onOpenDetails: () => void;
  onOpenExpand: () => void;
  onCloseExpand: () => void;
}

function GridActionToolbar({
  caption,
  warnings,
  queryErrors,
  details,
  expandOpen,
  density,
  onDensityChange,
  onOpenDetails,
  onOpenExpand,
  onCloseExpand,
}: GridActionToolbarProps) {
  const apiRef = useGridApiContext();
  const [searchAnchorEl, setSearchAnchorEl] = useState<HTMLElement | null>(
    null,
  );
  const [searchText, setSearchText] = useState('');

  const showColumns = () => {
    apiRef.current.showPreferences(GridPreferencePanelsValue.columns);
  };

  const showFilters = () => {
    apiRef.current.showPreferences(GridPreferencePanelsValue.filters);
  };

  const exportCsv = () => {
    apiRef.current.exportDataAsCsv();
  };

  const cycleDensity = () => {
    const nextDensity =
      density === 'compact'
        ? 'standard'
        : density === 'standard'
          ? 'comfortable'
          : 'compact';
    onDensityChange(nextDensity);
  };

  const openSearch = (event: React.MouseEvent<HTMLElement>) => {
    setSearchAnchorEl(event.currentTarget);
  };

  const closeSearch = () => {
    setSearchAnchorEl(null);
  };

  const updateSearch = (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setSearchText(value);
    apiRef.current.setQuickFilterValues(value.split(/\s+/).filter(Boolean));
  };

  return (
    <GridToolbarContainer
      sx={{
        minHeight: 44,
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        px: 1,
        py: 0.5,
        flexWrap: 'nowrap',
      }}
    >
      {caption && (
        <Typography
          variant="subtitle1"
          component="div"
          title={caption}
          sx={{
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 500,
            mr: 1,
          }}
        >
          {caption}
        </Typography>
      )}
      <Box sx={{ flex: 1, minWidth: 0 }} />
      {(warnings.length > 0 || queryErrors.length > 0) && (
        <QueryValidationBadge errors={queryErrors} warnings={warnings} />
      )}
      <Tooltip title="Search">
        <IconButton size="small" onClick={openSearch}>
          <Search fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Columns">
        <IconButton size="small" onClick={showColumns}>
          <ViewColumn fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Filters">
        <IconButton size="small" onClick={showFilters}>
          <FilterList fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title={`Density: ${density}`}>
        <IconButton size="small" onClick={cycleDensity}>
          <DensitySmall fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Export CSV">
        <IconButton size="small" onClick={exportCsv}>
          <Download fontSize="small" />
        </IconButton>
      </Tooltip>
      {details !== undefined && (
        <Tooltip title="Show query details">
          <IconButton size="small" onClick={onOpenDetails}>
            <Info fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      {expandOpen === false ? (
        <Tooltip title="Fullscreen">
          <IconButton size="small" onClick={onOpenExpand}>
            <Fullscreen fontSize="small" />
          </IconButton>
        </Tooltip>
      ) : (
        <Tooltip title="Close Fullscreen">
          <IconButton size="small" onClick={onCloseExpand}>
            <CloseFullscreen fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      <Popover
        open={Boolean(searchAnchorEl)}
        anchorEl={searchAnchorEl}
        onClose={closeSearch}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <TextField
          autoFocus
          size="small"
          placeholder="Search"
          value={searchText}
          onChange={updateSearch}
          sx={{ m: 1, width: 240 }}
        />
      </Popover>
    </GridToolbarContainer>
  );
}

export default function CypherTable({
  cypher,
  params,
  columns,
  caption,
  needInputs,
  details,
  height,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
  preloadedRecords,
}: CypherTableProps) {
  const cspNonce = getCspNonce();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [expandOpen, setExpandOpen] = useState(false);
  const [density, setDensity] = useState<GridDensity>('compact');
  const [expandSize, setExpandSize] = useState(window.innerHeight);
  const [containerHeight, setContainerHeight] = useState<number | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  // When preloadedRecords is provided, pass undefined cypher so the hook never fetches.
  const [
    runQuery,
    {
      loading: fetchLoading,
      error,
      records: fetchedRecords,
      warnings,
      queryErrors,
      tokenExpired,
    },
  ] = useLazyCypherQuery(
    preloadedRecords !== undefined ? undefined : cypher,
    reportQueryToken,
  );

  const records = preloadedRecords ?? fetchedRecords;
  const first = records?.[0];
  const loading = preloadedRecords !== undefined ? false : fetchLoading;

  // Callback ref so the ResizeObserver re-attaches whenever the container DOM
  // node swaps — the component renders different outer Boxes for the loading,
  // empty, and loaded states.
  const containerRef = useCallback((node: HTMLDivElement | null) => {
    if (typeof ResizeObserver === 'undefined') return;
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setContainerHeight(entry.contentRect.height);
    });
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  useEffect(() => {
    function handleResize() {
      setExpandSize(window.innerHeight);
    }

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    return () => {
      observerRef.current?.disconnect();
      observerRef.current = null;
    };
  }, []);

  const runQueryRef = useRef(runQuery);
  runQueryRef.current = runQuery;
  const needInputsRef = useRef(needInputs);
  needInputsRef.current = needInputs;

  useEffect(() => {
    if (
      needInputsRef.current === undefined ||
      needInputsRef.current.length === 0
    ) {
      runQueryRef.current(params, { force: (refreshKey ?? 0) > 0 });
    }
  }, [cypher, params, refreshKey]);

  useEffect(() => {
    if (tokenExpired) {
      onTokenExpired?.();
    }
  }, [tokenExpired, onTokenExpired]);

  if (error) {
    console.log(error);
    return (
      <Box ref={containerRef} sx={fillSx}>
        <Typography variant="body2">
          Failed to load requested data, please reload.
        </Typography>
      </Box>
    );
  }

  if (preloadedRecords === undefined && cypher === undefined) {
    return (
      <Box ref={containerRef} sx={fillSx}>
        <Error />
        <Typography variant="body2">Missing cypher query</Typography>
      </Box>
    );
  }

  if (queryErrors.length > 0) {
    return (
      <Box ref={containerRef} sx={fillSx}>
        <Typography
          variant="subtitle1"
          component="div"
          sx={{ fontWeight: 500, mb: 0.5 }}
        >
          {caption}
          <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        </Typography>
        <Typography variant="body2">Query failed.</Typography>
      </Box>
    );
  }

  const setOpenDetails = () => {
    setDetailsOpen(true);
  };

  const setOpenExpand = () => {
    setExpandOpen(true);
  };

  const setClosedExpand = () => {
    setExpandOpen(false);
  };

  let tableBodyHeight: string;
  let tableBodySkeletonHeight: string;

  // Defined inline so it can close over the panel's toolbar state; passed to
  // the MUI DataGrid `toolbar` slot, which expects a component reference.
  // eslint-disable-next-line @eslint-react/no-nested-component-definitions
  const ToolbarActions = () => (
    <GridActionToolbar
      caption={caption}
      warnings={warnings}
      queryErrors={queryErrors}
      details={details}
      expandOpen={expandOpen}
      density={density}
      onDensityChange={setDensity}
      onOpenDetails={setOpenDetails}
      onOpenExpand={setOpenExpand}
      onCloseExpand={setClosedExpand}
    />
  );

  if (expandOpen) {
    // window height minus the size of the table header and footer
    tableBodyHeight = `${expandSize - 225}px`;
    tableBodySkeletonHeight = tableBodyHeight;
  } else if (height !== undefined) {
    tableBodyHeight = height;
    tableBodySkeletonHeight = height;
  } else if (containerHeight !== null) {
    // Fill the parent cell, leaving room for the Data Grid chrome and
    // (when present) the caption rendered above the table.
    const chrome =
      TABLE_CHROME_HEIGHT + (loading && caption ? CAPTION_HEIGHT : 0);
    const bodyPx = Math.max(containerHeight - chrome, MIN_TABLE_BODY_HEIGHT);
    tableBodyHeight = `${bodyPx}px`;
    tableBodySkeletonHeight = tableBodyHeight;
  } else {
    // First render before ResizeObserver fires — use a sensible default.
    tableBodyHeight = `${MIN_TABLE_BODY_HEIGHT}px`;
    tableBodySkeletonHeight = tableBodyHeight;
  }

  const renderGrid = (
    rows: GridRowsProp,
    gridColumns: GridColDef[],
    noRowsMessage = 'No rows',
  ) => (
    <Paper
      variant="outlined"
      sx={{ minHeight: 0, display: 'flex', flexDirection: 'column', flex: 1 }}
    >
      <Box sx={{ height: tableBodyHeight, minHeight: MIN_TABLE_BODY_HEIGHT }}>
        <DataGrid
          rows={rows}
          columns={gridColumns}
          nonce={cspNonce}
          getRowId={(row) => row.__rowId}
          density={density}
          disableColumnMenu
          disableRowSelectionOnClick
          pageSizeOptions={[10, 15, 100]}
          showToolbar
          initialState={{
            pagination: {
              paginationModel: { page: 0, pageSize: expandOpen ? 100 : 10 },
            },
          }}
          slots={{
            toolbar: ToolbarActions,
            noColumnsOverlay: () => <EmptyOverlay message={noRowsMessage} />,
            noRowsOverlay: () => <EmptyOverlay message={noRowsMessage} />,
          }}
        />
      </Box>
    </Paper>
  );

  if (needInputs !== undefined && needInputs.length > 0) {
    const noMatchMessage = `Select ${needInputs.join(', ')} to load results`;
    const gridColumns = makeGridColumns(columns ?? []);
    return (
      <Box ref={containerRef} sx={fillSx}>
        {renderGrid([], gridColumns, noMatchMessage)}
        <Dialog fullScreen open={expandOpen} onClose={setClosedExpand}>
          <DialogContent>
            {renderGrid([], gridColumns, noMatchMessage)}
          </DialogContent>
          <DialogActions>
            <Button onClick={setClosedExpand} color="primary" autoFocus>
              Close
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    );
  }

  if (loading || records === undefined) {
    return (
      <Box ref={containerRef} sx={fillSx}>
        {caption && (
          <Typography variant="subtitle1" sx={{ fontWeight: 500, mb: 0.5 }}>
            {caption}
          </Typography>
        )}
        <TablePanelSkeleton height={tableBodySkeletonHeight} />
      </Box>
    );
  }

  if (records === null || records.length === 0) {
    return (
      <Box ref={containerRef} sx={fillSx}>
        <Typography variant="body2">No records found.</Typography>
      </Box>
    );
  }

  if (first === undefined) {
    return (
      <Box ref={containerRef} sx={fillSx}>
        {renderGrid([], [], 'No rows')}
      </Box>
    );
  }

  const mungedColumns: Array<{ name: string; label: string }> = [];
  if (columns === undefined) {
    Object.keys(flattenRecord(first)).forEach((column) => {
      mungedColumns.push({ name: column, label: column });
    });
  } else {
    columns.forEach((column) => mungedColumns.push(column));
  }

  const mungedRecords: GridRow[] = [];
  for (let i = 0; i < records.length; i++) {
    const mungedData =
      columns === undefined ? flattenRecord(records[i]) : { ...records[i] };
    Object.keys(mungedData).forEach((key) => {
      const val = mungedData[key];
      if (typeof val === 'object' && val !== null) {
        mungedData[key] = formatValue(val);
      }
    });
    mungedRecords.push({ ...mungedData, __rowId: `row-${i}` });
  }
  const gridColumns = makeGridColumns(mungedColumns);

  return (
    <Box ref={containerRef} sx={fillSx}>
      {renderGrid(mungedRecords, gridColumns)}
      {details !== undefined && (
        <CypherDetails
          details={details}
          open={detailsOpen}
          setOpen={setDetailsOpen}
        />
      )}
      <Dialog fullScreen open={expandOpen} onClose={setClosedExpand}>
        <DialogContent>{renderGrid(mungedRecords, gridColumns)}</DialogContent>
        <DialogActions>
          <Button onClick={setClosedExpand} color="primary" autoFocus>
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
