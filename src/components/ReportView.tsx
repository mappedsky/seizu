import { useState, useEffect, useCallback, useMemo } from 'react';
import PageTitle from 'src/components/PageTitle';
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

import { Report, type InputValue } from 'src/config.context';
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
  /**
   * What the browser tab shows, without the " | Seizu" suffix. Defaults to the
   * report's own title; a caller that frames the report differently (one
   * specific version) says so here rather than mounting a second PageTitle, of
   * which only one may exist.
   */
  documentTitle?: string;
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

/**
 * Clearance between a pinned toolbar and the first row of content, in px.
 *
 * The spacer that stands in for the fixed toolbar has to be taller than the
 * toolbar itself: matching it exactly leaves the first row flush against the
 * bar, under its box-shadow, so the row reads as clipped. The title band this
 * component used to render supplied the same gap through its bottom margin.
 */
const STICKY_TOOLBAR_CONTENT_GAP = 16;

/** The values a report's inputs start at: the query string, else the declared default. */
function buildInitialVarData(
  report: Report,
): Record<string, InputValue | undefined> {
  const values: Record<string, InputValue | undefined> = {};
  report.inputs?.forEach((input) => {
    const inputValue = getQueryStringValue(input.input_id);
    const scalarInputValue = Array.isArray(inputValue)
      ? inputValue.find((value): value is string => value !== null)
      : inputValue;
    if (typeof scalarInputValue === 'string') {
      // TODO(ryan-lane): Figure out a way to pass the label along with the value in the param
      values[input.input_id] = {
        label: scalarInputValue,
        value: scalarInputValue,
      };
    } else if (input.default !== undefined) {
      values[input.input_id] = input.default;
    } else {
      values[input.input_id] = undefined;
    }
  });
  return values;
}

function ReportView({
  report,
  title,
  showTitle = false,
  documentTitle,
  boxSx = { minHeight: '100%', pb: 3 },
  queryCapabilities,
  toolbarActions,
  stickyToolbar = true,
  onRefreshCapabilities,
}: ReportViewProps) {
  const displayTitle = title ?? report.name;
  const tabTitle = documentTitle ?? displayTitle;
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
  // The inputs a report starts with are computed from the report and the
  // query string, not copied into state by an effect: a report that changes
  // takes its own defaults by being read past, rather than rendering the
  // previous report's values for a frame first.
  const initialVarData = useMemo(() => buildInitialVarData(report), [report]);
  const [editedVarData, setEditedVarData] = useState<{
    report: Report;
    values: Record<string, InputValue | undefined>;
  } | null>(null);
  const varData =
    editedVarData !== null && editedVarData.report === report
      ? editedVarData.values
      : initialVarData;
  const setVarData = useCallback(
    (values: Record<string, InputValue | undefined>) =>
      setEditedVarData({ report, values }),
    [report],
  );
  const [toolbarHeight, setToolbarHeight] = useState(64);
  const [collapsedRows, setCollapsedRows] = useState<Record<number, boolean>>(
    {},
  );

  // What the last seen capability set was, when it arrived, and how many
  // forced re-runs have been issued — one value, because the three only ever
  // change together.
  const [refreshState, setRefreshState] = useState<{
    seen: Record<string, string> | undefined;
    at: Date | undefined;
    key: number;
    awaitingTokens: boolean;
  }>({ seen: undefined, at: undefined, key: 0, awaitingTokens: false });

  // Adjusted during render rather than from an effect: this is state derived
  // from an input that changed, and React re-runs the component with the new
  // state before committing, so it costs no extra paint. The updater is pure,
  // so a second invocation (StrictMode, a replayed render) is a no-op rather
  // than a second forced re-run.
  if (
    queryCapabilities !== undefined &&
    queryCapabilities !== refreshState.seen
  ) {
    setRefreshState((previous) => ({
      seen: queryCapabilities,
      at: new Date(),
      // Not the first arrival, and a panel asked for new tokens: bump the key
      // so every panel re-runs its query against them.
      key:
        previous.at !== undefined && previous.awaitingTokens
          ? previous.key + 1
          : previous.key,
      awaitingTokens: false,
    }));
  }

  const refreshKey = refreshState.key;
  const refreshedAt = refreshState.at;

  const handleRefresh = useCallback(() => {
    setRefreshState((previous) => ({
      ...previous,
      at: new Date(),
      key: previous.key + 1,
    }));
  }, []);

  // Called by any panel that receives a token_expired response.
  // Triggers a capabilities re-fetch; when the new capabilities arrive, the
  // watch above bumps refreshKey so all panels retry.
  const onTokenExpired = useCallback(() => {
    setRefreshState((previous) =>
      previous.awaitingTokens
        ? previous
        : { ...previous, awaitingTokens: true },
    );
    onRefreshCapabilities?.();
  }, [onRefreshCapabilities]);

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

  // The toolbar is up to two rows: title + actions, then inputs. Each is
  // skipped when empty so a report never gets a blank bar, and the title counts
  // as content so a title-only report still gets a proper header.
  const hasHeaderRow =
    Boolean(showTitle && displayTitle) || toolbarActions !== undefined;
  const hasInputsRow = inputControls.length > 0;
  const hasToolbarContent = hasHeaderRow || hasInputsRow;
  const isSticky = stickyToolbar && hasToolbarContent;

  // A callback ref rather than a layout effect: the spacer that reserves room
  // for the fixed toolbar must be the right height on the first paint, and a
  // ref callback runs at the same point in the commit without measuring from
  // inside an effect. React 19 calls the returned cleanup when the node goes.
  const toolbarRef = useCallback((node: HTMLDivElement | null) => {
    if (node === null) return undefined;
    const measure = () => setToolbarHeight(node.offsetHeight);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

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
        mb: isSticky ? 0 : 2,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Row 1: title and actions. The title lives here rather than in a band
          of its own, so a report gets one header row instead of two, and it
          truncates rather than wrapping so a long name cannot push the actions
          off the row. */}
      {hasHeaderRow && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 1.5,
            py: 2,
            minWidth: 0,
          }}
        >
          {showTitle && displayTitle && (
            <Typography
              component="h1"
              variant="h2"
              title={displayTitle}
              sx={{
                lineHeight: 1.25,
                flex: '1 1 auto',
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {displayTitle}
            </Typography>
          )}
          {toolbarActions && (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-end',
                gap: 1,
                flex: showTitle && displayTitle ? '0 0 auto' : '1 1 auto',
                flexWrap: 'wrap',
                ml: showTitle && displayTitle ? 0 : 'auto',
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
      )}
      {/* Row 2: report inputs get their own bar. Sharing the row with the title
          and actions left too little space once a report had more than one
          input. */}
      {hasInputsRow && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            flexWrap: 'wrap',
            minWidth: 0,
            pt: hasHeaderRow ? 0 : 2,
            pb: 2,
            ...(hasHeaderRow
              ? { borderTop: 1, borderColor: 'divider', mt: 0, pt: 1.5 }
              : {}),
          }}
        >
          {inputControls}
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
      {tabTitle && <PageTitle>{tabTitle} | Seizu</PageTitle>}
      <Box sx={boxSx}>
        {hasToolbarContent && toolbar}
        {isSticky && (
          <Box sx={{ height: toolbarHeight + STICKY_TOOLBAR_CONTENT_GAP }} />
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
