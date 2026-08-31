import {
  isValidElement,
  type MouseEvent as ReactMouseEvent,
  ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Badge,
  Box,
  Button,
  Checkbox,
  Divider,
  IconButton,
  InputAdornment,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Typography,
} from '@mui/material';
import AllInclusiveIcon from '@mui/icons-material/AllInclusive';
import CheckIcon from '@mui/icons-material/Check';
import FilterAltIcon from '@mui/icons-material/FilterAlt';
import SearchIcon from '@mui/icons-material/Search';
import Clear from '@mui/icons-material/Clear';
import type { Breakpoint, SxProps, Theme } from '@mui/material/styles';

type ColumnAlign = 'inherit' | 'left' | 'center' | 'right' | 'justify';
type ColumnWidth = number | string;

export interface ListTableColumn<T> {
  key: string;
  label?: ReactNode;
  align?: ColumnAlign;
  width?: ColumnWidth;
  minWidth?: number;
  resizable?: boolean;
  cellSx?: SxProps<Theme>;
  headerSx?: SxProps<Theme>;
  hideBelow?: Exclude<Breakpoint, 'xs'>;
  render: (row: T) => ReactNode;
}

export interface ListTableFilterOption<T> {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  matches: (row: T) => boolean;
}

export interface ListTableFilterGroup<T> {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  options: ListTableFilterOption<T>[];
}

interface ListTableProps<T> {
  rows: T[];
  columns: ListTableColumn<T>[];
  getRowKey: (row: T) => string | number;
  emptyMessage: ReactNode;
  searchLabel?: string;
  searchPlaceholder?: string;
  filterGroups?: ListTableFilterGroup<T>[];
  pagination?: boolean;
  initialRowsPerPage?: number;
  rowsPerPageOptions?: number[];
  /**
   * Opt in to row selection: adds a checkbox column and swaps the toolbar for a
   * selection bar while anything is selected. Selection is controlled by the
   * caller so bulk actions can read it directly.
   */
  selectable?: boolean;
  selectedKeys?: ReadonlyArray<string | number>;
  onSelectionChange?: (keys: Array<string | number>) => void;
  /**
   * Rendered in the selection bar. Rows a given action cannot apply to are the
   * action's problem, not the table's — the backend enforces per-row rules
   * anyway, so everything stays selectable.
   */
  bulkActions?: (selectedRows: T[]) => ReactNode;
}

const LIST_TABLE_ROWS_PER_PAGE_STORAGE_PREFIX =
  'seizu:list-table:rows-per-page';

function hideBelowSx(
  hideBelow: ListTableColumn<unknown>['hideBelow'],
): SxProps<Theme> {
  if (!hideBelow) return {};
  return {
    display: {
      xs: 'none',
      [hideBelow]: 'table-cell',
    },
  };
}

function mergeSx(...items: Array<SxProps<Theme> | undefined>): SxProps<Theme> {
  return items.filter(Boolean) as SxProps<Theme>;
}

function extractWidthSx(
  sx: SxProps<Theme> | undefined,
): ColumnWidth | undefined {
  if (!sx || Array.isArray(sx) || typeof sx === 'function') return undefined;
  const width = (sx as Record<string, unknown>).width;
  return typeof width === 'number' || typeof width === 'string'
    ? width
    : undefined;
}

const COLUMN_MIN_WIDTH = 48;

// A column with no declared width shares whatever the sized columns leave over.
// Under `table-layout: fixed` that share can fall to a few pixels, so give it a
// floor wide enough to still read as text and let the container scroll instead.
const FLEXIBLE_COLUMN_MIN_WIDTH = 120;

const BREAKPOINT_ORDER = ['xs', 'sm', 'md', 'lg', 'xl'] as const;

function isVisibleAt(
  hideBelow: ListTableColumn<unknown>['hideBelow'],
  breakpoint: Breakpoint,
): boolean {
  if (!hideBelow) return true;
  return (
    BREAKPOINT_ORDER.indexOf(breakpoint) >= BREAKPOINT_ORDER.indexOf(hideBelow)
  );
}

function isPixelWidth(width: ColumnWidth | undefined): boolean {
  if (typeof width === 'number') return true;
  return typeof width === 'string' && width.trim().endsWith('px');
}

function widthToCss(width: ColumnWidth): string {
  return typeof width === 'number' ? `${width}px` : width;
}

function getNodeTextContent(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean')
    return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) {
    return node
      .map((child) => getNodeTextContent(child))
      .filter(Boolean)
      .join(' ');
  }
  if (isValidElement(node)) {
    const props = node.props as {
      children?: ReactNode;
      label?: ReactNode;
      title?: ReactNode;
      ['aria-label']?: ReactNode;
    };
    return [
      getNodeTextContent(props.label),
      getNodeTextContent(props.title),
      getNodeTextContent(props['aria-label']),
      getNodeTextContent(props.children),
    ]
      .filter(Boolean)
      .join(' ');
  }
  return '';
}

function normalizeFilterText(text: string): string {
  return text.trim().toLowerCase();
}

function getRowsPerPageStorageKey(): string {
  return `${LIST_TABLE_ROWS_PER_PAGE_STORAGE_PREFIX}:${window.location.pathname}`;
}

function getStoredRowsPerPage(
  storageKey: string,
  fallback: number,
  allowedValues: number[],
): number {
  const storedValue = window.localStorage.getItem(storageKey);
  if (!storedValue) return fallback;

  const parsedValue = Number.parseInt(storedValue, 10);
  if (!Number.isFinite(parsedValue) || parsedValue <= 0) return fallback;
  if (allowedValues.length > 0 && !allowedValues.includes(parsedValue))
    return fallback;

  return parsedValue;
}

function isResizingDisabled(column: ListTableColumn<unknown>): boolean {
  if (column.resizable !== undefined) return !column.resizable;
  return column.key === 'actions' || column.key === 'row_actions';
}

const selectionColumnSx = {
  width: 48,
  minWidth: 48,
} as const;

export const listTableActionColumnSx = {
  width: 48,
  minWidth: 48,
  pr: 1,
} as const;

export const listTablePrimaryCellSx = {
  minWidth: 0,
} as const;

export const listTableSecondaryCellSx = {
  color: 'text.secondary',
} as const;

export const listTableMonoCellSx = {
  color: 'text.secondary',
  fontFamily: 'monospace',
} as const;

export const listTableTruncateSx = {
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  minWidth: 0,
} as const;

const listTableCellContentSx = {
  ...listTableTruncateSx,
  display: 'block',
  maxWidth: '100%',
} as const;

const listTableHeaderContentSx = {
  ...listTableCellContentSx,
  display: 'flex',
  alignItems: 'center',
  gap: 0.5,
} as const;

function TableCellHoverTooltip({
  content,
  children,
}: {
  content: ReactNode;
  children: ReactNode;
}) {
  const tooltip = getNodeTextContent(content);
  if (!tooltip) return <>{children}</>;

  return (
    <Tooltip title={tooltip} placement="top" arrow disableInteractive>
      <Box component="span" sx={{ display: 'block', minWidth: 0 }}>
        {children}
      </Box>
    </Tooltip>
  );
}

export default function ListTable<T>({
  rows,
  columns,
  getRowKey,
  emptyMessage,
  searchLabel = 'Search',
  searchPlaceholder = 'Search rows',
  filterGroups = [],
  pagination = true,
  initialRowsPerPage = 10,
  rowsPerPageOptions = [10, 25, 50, 75, 100],
  selectable = false,
  selectedKeys = [],
  onSelectionChange,
  bulkActions,
}: ListTableProps<T>) {
  const [page, setPage] = useState(0);
  const rowsPerPageStorageKey = useMemo(getRowsPerPageStorageKey, []);
  const [rowsPerPage, setRowsPerPage] = useState(() => {
    if (typeof window === 'undefined') return initialRowsPerPage;
    return getStoredRowsPerPage(
      rowsPerPageStorageKey,
      initialRowsPerPage,
      rowsPerPageOptions,
    );
  });
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
  const [filterText, setFilterText] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [filterAnchorEl, setFilterAnchorEl] = useState<HTMLElement | null>(
    null,
  );
  const [selectedFilterKeys, setSelectedFilterKeys] = useState<
    Record<string, string[]>
  >({});
  const paginationEnabled = pagination && rows.length > 0;
  const headerCellRefs = useRef<Record<string, HTMLElement | null>>({});
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const resizeDragRef = useRef<{
    targetIndex: number;
    startX: number;
    baseWidths: Record<string, number>;
  } | null>(null);
  const resizeListenersRef = useRef<{
    handleMouseMove: (event: MouseEvent) => void;
    handleMouseUp: () => void;
  } | null>(null);

  useEffect(() => {
    const maxPage = Math.max(Math.ceil(rows.length / rowsPerPage) - 1, 0);
    if (page > maxPage) {
      setPage(maxPage);
    }
  }, [page, rows.length, rowsPerPage]);

  useEffect(() => {
    setPage(0);
  }, [filterText, selectedFilterKeys]);

  useEffect(() => {
    if (searchOpen) {
      window.setTimeout(() => {
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }, 0);
    }
  }, [searchOpen]);

  useEffect(() => {
    window.localStorage.setItem(rowsPerPageStorageKey, String(rowsPerPage));
  }, [rowsPerPage, rowsPerPageStorageKey]);

  useEffect(
    () => () => {
      if (resizeListenersRef.current) {
        const { handleMouseMove, handleMouseUp } = resizeListenersRef.current;
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
        resizeListenersRef.current = null;
      }
      resizeDragRef.current = null;
    },
    [],
  );

  const filteredRows = useMemo(() => {
    const normalizedFilter = normalizeFilterText(filterText);
    const activeFilterEntries = Object.entries(selectedFilterKeys).filter(
      ([, value]) => value.length > 0,
    );
    if (!normalizedFilter && activeFilterEntries.length === 0) return rows;

    return rows.filter((row) => {
      const matchesSearch =
        !normalizedFilter ||
        columns
          .map((column) => getNodeTextContent(column.render(row)))
          .join(' ')
          .toLowerCase()
          .includes(normalizedFilter);

      if (!matchesSearch) return false;

      return filterGroups.every((group) => {
        const selectedKeys = selectedFilterKeys[group.key] ?? [];
        if (selectedKeys.length === 0) return true;
        return selectedKeys.some((selectedKey) => {
          const option = group.options.find(
            (candidate) => candidate.key === selectedKey,
          );
          return option ? option.matches(row) : false;
        });
      });
    });
  }, [columns, filterGroups, filterText, rows, selectedFilterKeys]);

  const visibleRows = useMemo(() => {
    if (!pagination) return filteredRows;
    const start = page * rowsPerPage;
    return filteredRows.slice(start, start + rowsPerPage);
  }, [filteredRows, page, pagination, rowsPerPage]);

  // Keyed on the array identity, not a joined string: a delimiter cannot be
  // chosen that no key contains, and joining collides across types (numeric 1
  // and string "1"), which would leave a stale Set. Callers hold selection in
  // state and replace the array on change, so the identity is stable.
  const selectedKeySet = useMemo(() => new Set(selectedKeys), [selectedKeys]);

  const selectedRows = useMemo(
    () =>
      selectable
        ? filteredRows.filter((row) => selectedKeySet.has(getRowKey(row)))
        : [],
    [selectable, filteredRows, selectedKeySet, getRowKey],
  );

  // Drop selections that are no longer reachable — a row that was deleted, or
  // filtered out — so a bulk action can never act on something off screen.
  useEffect(() => {
    if (!selectable || !onSelectionChange || selectedKeySet.size === 0) return;
    const reachable = new Set(filteredRows.map(getRowKey));
    const pruned = [...selectedKeySet].filter((key) => reachable.has(key));
    if (pruned.length !== selectedKeySet.size) {
      onSelectionChange(pruned);
    }
  }, [selectable, onSelectionChange, selectedKeySet, filteredRows, getRowKey]);

  const toggleRowSelection = (row: T) => {
    if (!onSelectionChange) return;
    const key = getRowKey(row);
    const next = new Set(selectedKeySet);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange([...next]);
  };

  // The header checkbox covers the current page only, matching MUI's own
  // paginated-selection example: acting on rows you cannot see is worse than
  // needing a second click.
  const pageKeys = visibleRows.map(getRowKey);
  const allPageSelected =
    pageKeys.length > 0 && pageKeys.every((key) => selectedKeySet.has(key));
  const somePageSelected =
    !allPageSelected && pageKeys.some((key) => selectedKeySet.has(key));

  const togglePageSelection = () => {
    if (!onSelectionChange) return;
    const next = new Set(selectedKeySet);
    if (allPageSelected) pageKeys.forEach((key) => next.delete(key));
    else pageKeys.forEach((key) => next.add(key));
    onSelectionChange([...next]);
  };

  const clearSelection = () => onSelectionChange?.([]);
  const selectionActive = selectable && selectedKeySet.size > 0;

  const getDeclaredWidth = (
    column: ListTableColumn<T>,
  ): ColumnWidth | undefined =>
    column.width ??
    extractWidthSx(column.cellSx) ??
    extractWidthSx(column.headerSx);

  const getColumnMinWidth = (column: ListTableColumn<T>): number => {
    if (column.minWidth !== undefined)
      return Math.max(COLUMN_MIN_WIDTH, column.minWidth);
    // A pixel width is a column asking for a size and keeping it. A percentage
    // still flexes with the container, so it needs the flexible floor.
    const declared = getDeclaredWidth(column);
    if (typeof declared === 'number' || isPixelWidth(declared))
      return COLUMN_MIN_WIDTH;
    return FLEXIBLE_COLUMN_MIN_WIDTH;
  };

  const getColumnWidth = (column: ListTableColumn<T>): number => {
    const explicitWidth = columnWidths[column.key];
    if (explicitWidth !== undefined) return explicitWidth;

    const measuredWidth =
      headerCellRefs.current[column.key]?.getBoundingClientRect().width ?? 0;
    if (measuredWidth > 0) return Math.round(measuredWidth);

    if (typeof column.width === 'number') return column.width;
    if (
      typeof column.width === 'string' &&
      column.width.trim().endsWith('px')
    ) {
      const parsed = Number.parseFloat(column.width);
      if (Number.isFinite(parsed)) return parsed;
    }

    return 0;
  };

  // Chrome ignores `min-width` on cells under `table-layout: fixed`, so a
  // shortfall comes out of the width-less columns until they are unreadable.
  // Give the table itself a minimum instead and let the container scroll.
  //
  // Percentage widths make that a solve rather than a sum: a column asking for
  // 24% takes 24% of whatever the table ends up being, so the pixel columns
  // have to fit in what is left over.
  const getBreakpointMinWidth = (breakpoint: Breakpoint): number => {
    let pixelTotal = selectable ? COLUMN_MIN_WIDTH : 0;
    let percentSum = 0;
    let percentMinTotal = 0;

    for (const column of columns) {
      if (!isVisibleAt(column.hideBelow, breakpoint)) continue;
      const minWidth = getColumnMinWidth(column);
      const assigned = columnWidths[column.key] ?? getDeclaredWidth(column);

      if (typeof assigned === 'number') {
        pixelTotal += Math.max(assigned, minWidth);
      } else if (isPixelWidth(assigned)) {
        pixelTotal += Math.max(Number.parseFloat(assigned as string), minWidth);
      } else if (
        typeof assigned === 'string' &&
        assigned.trim().endsWith('%')
      ) {
        percentSum += Number.parseFloat(assigned) / 100;
        percentMinTotal += minWidth;
      } else {
        pixelTotal += minWidth;
      }
    }

    // Whichever binds: the percentage columns taking their share, or those
    // columns bottoming out at their own floor.
    const withPercentShare =
      percentSum > 0 && percentSum < 1 ? pixelTotal / (1 - percentSum) : 0;
    return Math.ceil(Math.max(pixelTotal + percentMinTotal, withPercentShare));
  };

  const tableMinWidth = BREAKPOINT_ORDER.reduce<
    Partial<Record<Breakpoint, number>>
  >((acc, breakpoint) => {
    acc[breakpoint] = getBreakpointMinWidth(breakpoint);
    return acc;
  }, {});

  const snapshotColumnWidths = () =>
    columns.reduce<Record<string, number>>((acc, column) => {
      const width = getColumnWidth(column);
      if (width > 0) {
        acc[column.key] = width;
      }
      return acc;
    }, {});

  const redistributeWidths = (
    baseWidths: Record<string, number>,
    targetIndex: number,
    delta: number,
  ): Record<string, number> => {
    const nextWidths = { ...baseWidths };
    const targetColumn = columns[targetIndex];
    const targetMinWidth = getColumnMinWidth(targetColumn);
    const currentTargetWidth = baseWidths[targetColumn.key] ?? targetMinWidth;
    let targetWidth = Math.max(targetMinWidth, currentTargetWidth + delta);
    let appliedDelta = targetWidth - currentTargetWidth;

    const absorbShrink = (
      orderedColumns: ListTableColumn<T>[],
      amount: number,
    ): number => {
      let remaining = amount;
      for (const column of orderedColumns) {
        if (remaining <= 0) break;
        const currentWidth =
          nextWidths[column.key] ??
          baseWidths[column.key] ??
          getColumnMinWidth(column);
        const minWidth = getColumnMinWidth(column);
        const reducible = Math.max(0, currentWidth - minWidth);
        if (reducible === 0) continue;
        const reduction = Math.min(remaining, reducible);
        nextWidths[column.key] = currentWidth - reduction;
        remaining -= reduction;
      }
      return remaining;
    };

    const absorbGrowth = (
      orderedColumns: ListTableColumn<T>[],
      amount: number,
    ): number => {
      let remaining = amount;
      for (const column of orderedColumns) {
        if (remaining <= 0) break;
        const currentWidth =
          nextWidths[column.key] ??
          baseWidths[column.key] ??
          getColumnMinWidth(column);
        nextWidths[column.key] = currentWidth + remaining;
        remaining = 0;
      }
      return remaining;
    };

    const rightColumns = columns.slice(targetIndex + 1);
    const leftColumns = columns.slice(0, targetIndex).reverse();

    if (appliedDelta > 0) {
      const remainingAfterRight = absorbShrink(rightColumns, appliedDelta);
      const remainingAfterLeft =
        remainingAfterRight > 0
          ? absorbShrink(leftColumns, remainingAfterRight)
          : 0;
      appliedDelta -= remainingAfterLeft;
      targetWidth = currentTargetWidth + appliedDelta;
    } else if (appliedDelta < 0) {
      const growthAmount = -appliedDelta;
      const receiverColumns =
        rightColumns.length > 0 ? [rightColumns[0]] : leftColumns.slice(0, 1);
      const remainingAfterGrowth = absorbGrowth(receiverColumns, growthAmount);
      if (remainingAfterGrowth > 0) {
        targetWidth = currentTargetWidth;
      }
    }

    nextWidths[targetColumn.key] = targetWidth;
    return nextWidths;
  };

  const startResize = (
    column: ListTableColumn<T>,
    event: ReactMouseEvent<HTMLSpanElement>,
  ) => {
    if (isResizingDisabled(column)) return;
    if (resizeDragRef.current) return;
    const targetIndex = columns.findIndex(
      (candidate) => candidate.key === column.key,
    );
    if (targetIndex < 0) return;
    const baseWidths = snapshotColumnWidths();
    resizeDragRef.current = {
      targetIndex,
      startX: event.clientX,
      baseWidths,
    };

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const drag = resizeDragRef.current;
      if (!drag) return;
      const delta = moveEvent.clientX - drag.startX;
      setColumnWidths(
        redistributeWidths(drag.baseWidths, drag.targetIndex, delta),
      );
    };

    const handleMouseUp = () => {
      resizeDragRef.current = null;
      resizeListenersRef.current = null;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    resizeListenersRef.current = { handleMouseMove, handleMouseUp };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    event.preventDefault();
    event.stopPropagation();
  };

  const getWidthStyle = (column: ListTableColumn<T>): SxProps<Theme> => {
    const width = columnWidths[column.key] ?? getDeclaredWidth(column);
    const minWidth = widthToCss(getColumnMinWidth(column));
    if (width === undefined) return { minWidth };
    return {
      width: widthToCss(width),
      minWidth,
    };
  };

  const activeFilterOptionCount = Object.values(selectedFilterKeys).reduce(
    (sum, values) => sum + values.length,
    0,
  );
  const hasSearch = normalizeFilterText(filterText).length > 0;
  const hasActiveFilters = activeFilterOptionCount > 0;

  const closeSearch = () => {
    setSearchOpen(false);
    setFilterText('');
  };

  const toggleSearch = () => {
    if (searchOpen) {
      closeSearch();
      return;
    }
    setSearchOpen(true);
  };

  const openFilterMenu = (event: ReactMouseEvent<HTMLButtonElement>) => {
    setFilterAnchorEl(event.currentTarget);
  };

  const closeFilterMenu = () => {
    setFilterAnchorEl(null);
  };

  const clearFilterGroup = (groupKey: string) => {
    setSelectedFilterKeys((prev) => ({ ...prev, [groupKey]: [] }));
    closeFilterMenu();
  };

  const selectFilterOption = (groupKey: string, optionKey: string) => {
    setSelectedFilterKeys((prev) => ({
      ...prev,
      [groupKey]: prev[groupKey]?.includes(optionKey)
        ? prev[groupKey].filter((value) => value !== optionKey)
        : [...(prev[groupKey] ?? []), optionKey],
    }));
  };

  return (
    <Paper variant="outlined">
      {selectionActive && (
        <Box
          sx={{
            alignItems: 'center',
            bgcolor: 'action.selected',
            borderBottom: 1,
            borderColor: 'divider',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 1,
            px: 2,
            py: 1,
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {selectedKeySet.size} selected
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, ml: 1 }}>
            {bulkActions?.(selectedRows)}
          </Box>
          <Button size="small" onClick={clearSelection} sx={{ ml: 'auto' }}>
            Clear
          </Button>
        </Box>
      )}
      <Box
        sx={{
          px: 2,
          pt: searchOpen ? 2.25 : 0,
          pb: searchOpen ? 1.25 : 0,
          transition: 'padding 180ms ease',
        }}
      >
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: searchOpen ? 'flex-start' : 'center', minWidth: 0 }}
        >
          <Tooltip title={searchLabel} placement="top" arrow>
            <IconButton
              aria-label={searchLabel}
              onClick={toggleSearch}
              color={searchOpen || hasSearch ? 'primary' : 'default'}
              size="small"
            >
              <SearchIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Box
            sx={{
              overflow: 'visible',
              flex: searchOpen ? '0 0 360px' : '0 0 0px',
              width: searchOpen ? 360 : 0,
              opacity: searchOpen ? 1 : 0,
              transition:
                'width 180ms ease, opacity 180ms ease, flex-basis 180ms ease',
              minWidth: 0,
              position: 'relative',
              mt: searchOpen ? 0.25 : 0,
            }}
          >
            <Tooltip title={searchPlaceholder} placement="top" arrow>
              <Box component="span" sx={{ display: 'block' }}>
                <TextField
                  inputRef={searchInputRef}
                  fullWidth
                  size="small"
                  label={searchPlaceholder}
                  value={filterText}
                  onChange={(event) => setFilterText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                      closeSearch();
                    }
                  }}
                  slotProps={{
                    input: {
                      endAdornment: filterText ? (
                        <InputAdornment position="end">
                          <IconButton
                            aria-label="Clear search"
                            edge="end"
                            onClick={() => setFilterText('')}
                            size="small"
                          >
                            <Clear fontSize="small" />
                          </IconButton>
                        </InputAdornment>
                      ) : undefined,
                    },
                  }}
                />
              </Box>
            </Tooltip>
          </Box>
          {filterGroups.length > 0 && (
            <>
              <Badge
                color="primary"
                badgeContent={activeFilterOptionCount}
                invisible={activeFilterOptionCount === 0}
              >
                <Tooltip title="Filters" placement="top" arrow>
                  <IconButton
                    aria-label="Filters"
                    onClick={openFilterMenu}
                    color={hasActiveFilters ? 'primary' : 'default'}
                    size="small"
                  >
                    <FilterAltIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Badge>
              <Menu
                anchorEl={filterAnchorEl}
                open={Boolean(filterAnchorEl)}
                onClose={closeFilterMenu}
                slotProps={{ paper: { sx: { minWidth: 240 } } }}
              >
                {filterGroups.map((group, groupIndex) => {
                  const selectedKeys = selectedFilterKeys[group.key] ?? [];
                  const allSelected = selectedKeys.length === 0;
                  return (
                    <Box key={group.key}>
                      {groupIndex > 0 && <Divider />}
                      <Box sx={{ px: 2, pt: 1.25, pb: 0.5 }}>
                        <Box
                          sx={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 0.75,
                          }}
                        >
                          {group.icon}
                          <Typography
                            variant="overline"
                            color="text.secondary"
                            sx={{ lineHeight: 1 }}
                          >
                            {group.label}
                          </Typography>
                        </Box>
                      </Box>
                      <MenuItem
                        onClick={() => clearFilterGroup(group.key)}
                        selected={allSelected}
                      >
                        <ListItemIcon>
                          {allSelected ? (
                            <CheckIcon fontSize="small" />
                          ) : (
                            <AllInclusiveIcon fontSize="small" />
                          )}
                        </ListItemIcon>
                        <ListItemText primary="All" />
                      </MenuItem>
                      {group.options.map((option) => (
                        <MenuItem
                          key={option.key}
                          onClick={() =>
                            selectFilterOption(group.key, option.key)
                          }
                          selected={selectedKeys.includes(option.key)}
                        >
                          <ListItemIcon>
                            {selectedKeys.includes(option.key) ? (
                              <CheckIcon fontSize="small" />
                            ) : (
                              (option.icon ?? (
                                <Box sx={{ width: 20, height: 20 }} />
                              ))
                            )}
                          </ListItemIcon>
                          <ListItemText primary={option.label} />
                        </MenuItem>
                      ))}
                    </Box>
                  );
                })}
              </Menu>
            </>
          )}
        </Stack>
      </Box>
      <TableContainer sx={{ overflowX: 'auto' }}>
        <Table
          sx={{ tableLayout: 'fixed', width: '100%', minWidth: tableMinWidth }}
        >
          <TableHead>
            <TableRow>
              {selectable && (
                <TableCell padding="checkbox" sx={selectionColumnSx}>
                  <Checkbox
                    size="small"
                    checked={allPageSelected}
                    indeterminate={somePageSelected}
                    onChange={togglePageSelection}
                    disabled={pageKeys.length === 0}
                    slotProps={{
                      input: {
                        'aria-label': 'Select all rows on this page',
                      },
                    }}
                  />
                </TableCell>
              )}
              {columns.map((column) => (
                <TableCell
                  key={column.key}
                  align={column.align}
                  ref={(node) => {
                    headerCellRefs.current[column.key] =
                      node as HTMLElement | null;
                  }}
                  sx={mergeSx(
                    hideBelowSx(column.hideBelow),
                    column.cellSx,
                    column.headerSx,
                    getWidthStyle(column),
                    {
                      position: 'relative',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      '&:hover .list-table-resize-handle': {
                        opacity: 1,
                      },
                    },
                  )}
                >
                  <Box sx={{ ...listTableHeaderContentSx, pr: 1.5 }}>
                    <TableCellHoverTooltip content={column.label}>
                      <Box
                        component="span"
                        sx={{ ...listTableCellContentSx, flex: '1 1 auto' }}
                      >
                        {column.label}
                      </Box>
                    </TableCellHoverTooltip>
                    {!isResizingDisabled(column) && (
                      <Box
                        component="span"
                        onMouseDown={(event) => startResize(column, event)}
                        className="list-table-resize-handle"
                        sx={{
                          position: 'absolute',
                          top: 0,
                          right: 0,
                          bottom: 0,
                          width: 6,
                          cursor: 'col-resize',
                          opacity: 0,
                          transition: 'opacity 120ms ease',
                          '&::after': {
                            content: '""',
                            position: 'absolute',
                            top: 0,
                            bottom: 0,
                            left: '50%',
                            width: 1,
                            transform: 'translateX(-50%)',
                            bgcolor: 'divider',
                          },
                          '&:hover::after': {
                            bgcolor: 'primary.main',
                          },
                          '&:hover': {
                            opacity: 1,
                          },
                        }}
                      />
                    )}
                  </Box>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {filteredRows.length === 0 && (
              <TableRow>
                <TableCell colSpan={columns.length + (selectable ? 1 : 0)}>
                  <Box component="div" sx={listTableCellContentSx}>
                    <Typography color="text.secondary" sx={{ py: 1 }}>
                      {hasSearch || hasActiveFilters
                        ? 'No rows match your filters.'
                        : emptyMessage}
                    </Typography>
                  </Box>
                </TableCell>
              </TableRow>
            )}
            {visibleRows.map((row) => (
              <TableRow
                key={getRowKey(row)}
                hover
                selected={selectable && selectedKeySet.has(getRowKey(row))}
              >
                {selectable && (
                  <TableCell padding="checkbox" sx={selectionColumnSx}>
                    <Checkbox
                      size="small"
                      checked={selectedKeySet.has(getRowKey(row))}
                      onChange={() => toggleRowSelection(row)}
                      slotProps={{ input: { 'aria-label': 'Select row' } }}
                    />
                  </TableCell>
                )}
                {columns.map((column) => {
                  const cellContent = column.render(row);
                  return (
                    <TableCell
                      key={column.key}
                      align={column.align}
                      sx={mergeSx(
                        hideBelowSx(column.hideBelow),
                        column.cellSx,
                        getWidthStyle(column),
                        {
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        },
                      )}
                    >
                      <TableCellHoverTooltip content={cellContent}>
                        <Box component="div" sx={listTableCellContentSx}>
                          {cellContent}
                        </Box>
                      </TableCellHoverTooltip>
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {paginationEnabled && filteredRows.length > 0 && (
        <Box sx={{ borderTop: 1, borderColor: 'divider' }}>
          <TablePagination
            component="div"
            count={filteredRows.length}
            page={page}
            rowsPerPage={rowsPerPage}
            rowsPerPageOptions={rowsPerPageOptions}
            onPageChange={(_event, nextPage) => setPage(nextPage)}
            onRowsPerPageChange={(event) => {
              setRowsPerPage(parseInt(event.target.value, 10));
              setPage(0);
            }}
          />
        </Box>
      )}
    </Paper>
  );
}
