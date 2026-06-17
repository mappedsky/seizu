import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Helmet } from 'react-helmet';
import {
  Box,
  Collapse,
  Container,
  Divider,
  IconButton,
  Paper,
  Typography,
} from '@mui/material';
import Error from '@mui/icons-material/Error';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

import { Report } from 'src/config.context';
import { getQueryStringValue } from 'src/components/QueryString';
import CypherAutocomplete from 'src/components/reports/CypherAutocomplete';
import FreeTextInput from 'src/components/reports/FreeTextInput';
import PanelItem from 'src/components/reports/PanelItem';
import PanelGridRow from 'src/components/reports/PanelGridRow';
import {
  DASHBOARD_NAVBAR_HEIGHT,
  DASHBOARD_SIDEBAR_WIDTH_VAR,
} from 'src/components/dashboardLayoutConstants';
import { contentContainerSx } from 'src/theme/layout';

const EMPTY_QUERY_CAPABILITIES: Record<string, string> = {};

export interface RefreshControls {
  onRefresh: () => void;
  refreshedAtLabel: string | undefined;
}

interface ReportViewProps {
  report: Report;
  title?: string;
  showTitle?: boolean;
  boxSx?: object;
  queryCapabilities?: Record<string, string>;
  toolbarActions?: (controls: RefreshControls) => React.ReactNode;
  stickyToolbar?: boolean;
  onRefreshCapabilities?: () => void;
}

function inputWidth(size?: number) {
  if (size === undefined) return 220;
  return Math.min(Math.max(size * 70, 180), 420);
}

function ReportView({
  report,
  title,
  showTitle = false,
  boxSx = { minHeight: '100%', pb: 3 },
  queryCapabilities,
  toolbarActions,
  stickyToolbar = true,
  onRefreshCapabilities,
}: ReportViewProps) {
  const displayTitle = title ?? report.name;
  const reportQueries = useMemo(() => report.queries ?? {}, [report]);
  const reportRows = Array.isArray(report.rows) ? report.rows : [];
  const hasInvalidRows = !Array.isArray(report.rows);
  const capabilities = queryCapabilities ?? EMPTY_QUERY_CAPABILITIES;
  const resolveQuery = useCallback(
    (cypher: string | undefined): string | undefined => {
      if (cypher === undefined) return undefined;
      return reportQueries[cypher] ?? cypher;
    },
    [reportQueries],
  );
  const resolveCapability = useCallback(
    (path: string): string | undefined => capabilities[path],
    [capabilities],
  );
  const [varData, setVarData] = useState({});
  const toolbarRef = useRef<HTMLDivElement | null>(null);
  const [toolbarHeight, setToolbarHeight] = useState(64);
  const [collapsedRows, setCollapsedRows] = useState<Record<number, boolean>>(
    {},
  );

  // Track refresh state for decoupled data fetching
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<Date | undefined>(undefined);
  // True while waiting for onRefreshCapabilities to deliver new queryCapabilities
  const pendingTokenRefreshRef = useRef(false);
  // Tracks whether queryCapabilities has been seen for the first time on this mount
  const initializedRef = useRef(false);

  // Watch for queryCapabilities arriving or changing.
  // On initial arrival: record the load time.
  // On subsequent changes (token expiry recovery): increment refreshKey so panels
  // re-render with the new tokens and re-run their queries.
  useEffect(() => {
    if (queryCapabilities === undefined) return;

    if (!initializedRef.current) {
      initializedRef.current = true;
      setRefreshedAt(new Date());
      return;
    }

    if (pendingTokenRefreshRef.current) {
      pendingTokenRefreshRef.current = false;
      setRefreshKey((k) => k + 1);
    }
    setRefreshedAt(new Date());
  }, [queryCapabilities]);

  const handleRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
    setRefreshedAt(new Date());
  }, []);

  // Called by any panel that receives a token_expired response.
  // Triggers a capabilities re-fetch; when the new capabilities arrive the
  // useEffect above will increment refreshKey so all panels retry.
  const onTokenExpired = useCallback(() => {
    if (pendingTokenRefreshRef.current) return;
    pendingTokenRefreshRef.current = true;
    onRefreshCapabilities?.();
  }, [onRefreshCapabilities]);

  useEffect(() => {
    const initialVarState = {};
    if (report.inputs) {
      report.inputs.forEach((input) => {
        const inputValue = getQueryStringValue(input.input_id);
        if (inputValue !== undefined) {
          // TODO(ryan-lane): Figure out a way to pass the label along with the value in the param
          initialVarState[input.input_id] = {
            label: inputValue,
            value: inputValue,
          };
        } else if (input.default !== undefined) {
          initialVarState[input.input_id] = input.default;
        } else {
          initialVarState[input.input_id] = {};
        }
      });
    }
    setVarData(initialVarState);
  }, [report]);

  const inputControls: React.ReactNode[] = [];
  if (report.inputs) {
    report.inputs.forEach((input, index) => {
      if (input === undefined) {
        inputControls.push(
          <Box
            key={`undefined-input-${index}`}
            sx={{
              minWidth: 180,
              width: { xs: '100%', sm: inputWidth() },
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Error />
              <Typography>Undefined input</Typography>
            </Box>
          </Box>,
        );
        return;
      }

      let inputComponent;
      if (input.type === 'autocomplete') {
        inputComponent = (
          <CypherAutocomplete
            cypher={input.cypher}
            params={input.params}
            inputId={input.input_id}
            inputDefault={input.default}
            labelName={input.label}
            value={varData}
            setValue={setVarData}
            reportQueryToken={resolveCapability(`inputs.${index}.cypher`)}
            refreshKey={refreshKey}
            onTokenExpired={onTokenExpired}
            size="small"
          />
        );
      } else if (input.type === 'text') {
        inputComponent = (
          <FreeTextInput
            inputId={input.input_id}
            inputDefault={input.default}
            labelName={input.label}
            value={varData}
            setValue={setVarData}
            size="small"
          />
        );
      }

      inputControls.push(
        <Box
          key={input.input_id}
          sx={{
            flex: { xs: '1 1 100%', sm: `0 1 ${inputWidth(input.size)}px` },
            minWidth: { xs: '100%', sm: 180 },
            maxWidth: { xs: 'none', sm: inputWidth(input.size) },
          }}
        >
          {inputComponent}
        </Box>,
      );
    });
  }

  const hasInputsOrActions =
    inputControls.length > 0 || toolbarActions !== undefined;
  const isSticky = stickyToolbar && hasInputsOrActions;

  useEffect(() => {
    if (!isSticky || toolbarRef.current === null) return undefined;

    const updateToolbarHeight = () => {
      setToolbarHeight(toolbarRef.current?.offsetHeight ?? 64);
    };

    updateToolbarHeight();
    if (typeof ResizeObserver === 'undefined') return undefined;

    const observer = new ResizeObserver(updateToolbarHeight);
    observer.observe(toolbarRef.current);
    return () => observer.disconnect();
  }, [isSticky, inputControls.length, toolbarActions]);

  // Tick every 30 s so the relative "Updated X mins ago" label stays accurate.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  let refreshedAtLabel: string | undefined;
  if (refreshedAt) {
    const diffMins = Math.floor((now - refreshedAt.getTime()) / 60_000);
    if (diffMins < 1) {
      refreshedAtLabel = 'Updated just now';
    } else if (diffMins === 1) {
      refreshedAtLabel = 'Updated 1 min ago';
    } else {
      refreshedAtLabel = `Updated ${diffMins} mins ago`;
    }
  }

  const toolbar = (
    <Box
      ref={toolbarRef}
      sx={{
        position: isSticky ? 'fixed' : 'static',
        top: isSticky ? DASHBOARD_NAVBAR_HEIGHT : 'auto',
        right: isSticky ? 0 : 'auto',
        left: isSticky
          ? { xs: 0, lg: `var(${DASHBOARD_SIDEBAR_WIDTH_VAR})` }
          : 'auto',
        zIndex: isSticky ? (theme) => theme.zIndex.appBar - 1 : 'auto',
        bgcolor: 'background.paper',
        borderBottom: 1,
        borderColor: 'divider',
        boxShadow: isSticky ? 1 : 'none',
        ...contentContainerSx,
        py: 2,
        mb: isSticky ? 0 : 2,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 1.5,
        flexWrap: 'wrap',
      }}
    >
      {inputControls.length > 0 && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            flex: '1 1 320px',
            flexWrap: 'wrap',
            minWidth: 0,
          }}
        >
          {inputControls}
        </Box>
      )}
      {toolbarActions && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 1,
            flex: inputControls.length > 0 ? '0 0 auto' : '1 1 auto',
            flexWrap: 'wrap',
            ml: hasInputsOrActions ? 0 : 'auto',
            '& .MuiButton-root': {
              minHeight: 40,
            },
            '& .MuiIconButton-root': {
              height: 40,
              width: 40,
            },
            '& .MuiChip-root': {
              height: 32,
            },
          }}
        >
          {toolbarActions({ onRefresh: handleRefresh, refreshedAtLabel })}
        </Box>
      )}
    </Box>
  );

  const toggleRowCollapsed = useCallback((rowIndex: number) => {
    setCollapsedRows((prev) => ({ ...prev, [rowIndex]: !prev[rowIndex] }));
  }, []);

  const rows = reportRows.map((row, rowIndex) => {
    // collapsible defaults to true; only disabled when explicitly set false
    const effectiveCollapsible = row.collapsible !== false;
    const isCollapsed =
      effectiveCollapsible && collapsedRows[rowIndex] === true;
    const hideHeader = row.hide_header === true;

    const collapseBtn = effectiveCollapsible ? (
      <IconButton
        className="row-collapse-btn"
        size="small"
        onClick={() => toggleRowCollapsed(rowIndex)}
        aria-label={isCollapsed ? `Expand ${row.name}` : `Collapse ${row.name}`}
        aria-expanded={!isCollapsed}
        sx={{
          opacity: isCollapsed ? 1 : 0,
          transition: 'opacity 0.15s',
          '&:focus-visible': { opacity: 1 },
          flexShrink: 0,
        }}
      >
        <ExpandMoreIcon
          sx={{
            transition: 'transform 0.2s',
            transform: isCollapsed ? 'rotate(-90deg)' : 'none',
          }}
        />
      </IconButton>
    ) : null;

    const panelArea = (
      <Box sx={{ py: 1.5 }}>
        <PanelGridRow
          panels={row.panels}
          renderPanel={(item, index) => (
            <PanelItem
              rowIndex={rowIndex}
              index={index}
              item={item}
              varData={varData}
              allInputs={report.inputs ?? []}
              resolveQuery={resolveQuery}
              resolveCapability={resolveCapability}
              refreshKey={refreshKey}
              onTokenExpired={onTokenExpired}
            />
          )}
        />
      </Box>
    );

    return (
      <Container
        key={row.name}
        maxWidth={false}
        sx={{ ...contentContainerSx, pb: 1.5 }}
      >
        <Paper
          elevation={1}
          sx={{
            p: 1.5,
            // Remove top padding when header is hidden so the row is visually compact
            pt: hideHeader ? 0 : 1.5,
            ...(effectiveCollapsible && {
              '&:hover .row-collapse-btn': { opacity: 1 },
            }),
          }}
        >
          {hideHeader ? (
            // No title — show a minimal right-aligned toggle so the row can still be collapsed
            effectiveCollapsible && (
              <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                {collapseBtn}
              </Box>
            )
          ) : (
            <>
              <Box sx={{ display: 'flex', alignItems: 'center' }}>
                <Typography variant="h2" sx={{ mb: 0, flex: 1 }}>
                  {row.name}
                </Typography>
                {collapseBtn}
              </Box>
              <Divider sx={{ mt: 1, mb: 0 }} />
            </>
          )}
          {effectiveCollapsible ? (
            <Collapse in={!isCollapsed} timeout="auto">
              {panelArea}
            </Collapse>
          ) : (
            panelArea
          )}
        </Paper>
      </Container>
    );
  });

  return (
    <>
      {displayTitle && (
        <Helmet>
          <title>{displayTitle} | Seizu</title>
        </Helmet>
      )}
      <Box sx={boxSx}>
        {toolbar}
        {isSticky && <Box sx={{ height: toolbarHeight }} />}
        {showTitle && displayTitle && (
          <Box
            sx={{
              bgcolor: 'background.paper',
              borderBottom: 1,
              borderColor: 'divider',
              mb: 2,
            }}
          >
            <Container
              maxWidth={false}
              sx={{
                ...contentContainerSx,
                py: 1.75,
              }}
            >
              <Typography component="h1" variant="h2" sx={{ lineHeight: 1.25 }}>
                {displayTitle}
              </Typography>
            </Container>
          </Box>
        )}
        {hasInvalidRows && (
          <Container maxWidth={false} sx={{ ...contentContainerSx, pb: 1.5 }}>
            <Paper
              role="alert"
              elevation={1}
              sx={{
                p: 2,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: 'error.main',
              }}
            >
              <Error />
              <Typography>
                This report has an invalid configuration: rows must be an array,
                with panels nested under each row.
              </Typography>
            </Paper>
          </Container>
        )}
        <Box>{rows}</Box>
      </Box>
    </>
  );
}

export default ReportView;
