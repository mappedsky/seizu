import { useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  SortableContext,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import {
  Box,
  Button,
  Checkbox,
  Divider,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Paper,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, type Theme } from '@mui/material/styles';
import CloseIcon from '@mui/icons-material/Close';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircleOutlined';
import CreateNewFolderOutlinedIcon from '@mui/icons-material/CreateNewFolderOutlined';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import LibraryAddOutlinedIcon from '@mui/icons-material/LibraryAddOutlined';
import PostAddOutlinedIcon from '@mui/icons-material/PostAddOutlined';
import StarIcon from '@mui/icons-material/Star';
import RowMenu from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { listTableTruncateSx } from 'src/components/ListTable';
import SpaceConstellation from 'src/components/spaces/SpaceConstellation';
import {
  dropTargetSx,
  subspaceSortIds,
  useFiling,
  useReportDrag,
  useSubspaceDrop,
  useSubspaceSort,
} from 'src/components/spaces/spaceDnd';
import type { SpaceManagement } from 'src/components/spaces/useSpaceManagement';
import type { ReportListItem } from 'src/hooks/useReportsApi';
import type { SpaceTree, SubspaceItem } from 'src/hooks/useSpacesApi';
import { pageContentSx } from 'src/theme/layout';

interface SpaceContentsProps {
  tree: SpaceTree;
  manage: SpaceManagement;
  reportPath: (reportId: string) => string;
}

const MEASURE = '72ch';

const dark = (theme: Theme) => theme.palette.mode === 'dark';
/** Row lead-in before the checkbox: row padding, grip, gap. */
const ROW_PAD = 4;
const GRIP_WIDTH = 28;
const ROW_GAP = 8;
/** A small checkbox with its 4px padding. */
const CHECKBOX_WIDTH = 28;

/** What a section needs to offer selection and dragging. */
interface Selection {
  /** reports:write held: rows can be selected and dragged. */
  enabled: boolean;
  has: (report: ReportListItem) => boolean;
  toggle: (report: ReportListItem) => void;
  /** Select every report given, or clear them if all are selected. */
  toggleAll: (reports: ReportListItem[]) => void;
  /** What dragging `report` carries: the selection, if it is part of it. */
  carried: (report: ReportListItem) => ReportListItem[];
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/**
 * The space's own page: what it is, what it holds, and the actions that fill
 * it. Shown at the space root until an overview is pinned, and always at
 * `/contents`.
 */
export default function SpaceContents({
  tree,
  manage,
  reportPath,
}: SpaceContentsProps) {
  const { space, subspaces, reports } = tree;
  const overviewId = space.overview_report_id;
  const topLevel = reports.filter((r) => !r.subspace_id);
  const empty = reports.length === 0 && subspaces.length === 0;
  const anchorIndex = overviewId
    ? reports.findIndex((r) => r.report_id === overviewId)
    : null;

  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  // Derived, not synced: a report that left the space drops out of the
  // selection the moment the tree stops listing it.
  const selected = reports.filter((r) => selectedIds.has(r.report_id));
  const filing = useFiling();

  const selection: Selection = {
    enabled: manage.canWriteReports,
    has: (report) => selectedIds.has(report.report_id),
    toggle: (report) =>
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(report.report_id)) next.delete(report.report_id);
        else next.add(report.report_id);
        return next;
      }),
    toggleAll: (group) =>
      setSelectedIds((prev) => {
        const next = new Set(prev);
        const all = group.every((r) => next.has(r.report_id));
        group.forEach((r) =>
          all ? next.delete(r.report_id) : next.add(r.report_id),
        );
        return next;
      }),
    carried: (report) =>
      selectedIds.has(report.report_id) ? selected : [report],
  };
  const clearSelection = () => setSelectedIds(new Set());

  return (
    // Room under the last row for the selection bar when it is up.
    <Box sx={{ ...pageContentSx, pb: selected.length > 0 ? 12 : 6 }}>
      <Box
        component="header"
        sx={{
          display: 'flex',
          gap: { xs: 2, sm: 3 },
          alignItems: 'flex-start',
          pt: { xs: 1, sm: 2 },
          pb: 3,
        }}
      >
        <SpaceConstellation
          seed={space.space_id}
          groups={reports.map((r) => r.subspace_id)}
          anchorIndex={anchorIndex !== -1 ? anchorIndex : null}
          size={{ xs: 48, sm: 72 }}
        />
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography
              variant="h1"
              sx={{ minWidth: 0, overflowWrap: 'anywhere' }}
            >
              {space.name}
            </Typography>
            {manage.canWriteSpaces && (
              <Tooltip title="Edit name and description">
                <Button
                  size="small"
                  color="inherit"
                  onClick={manage.openEditSpace}
                  startIcon={<EditOutlinedIcon fontSize="small" />}
                  sx={{ flexShrink: 0, color: 'text.secondary' }}
                >
                  Edit
                </Button>
              </Tooltip>
            )}
          </Box>
          {space.description ? (
            <Typography
              color="text.secondary"
              sx={{ mt: 0.75, maxWidth: MEASURE }}
            >
              {space.description}
            </Typography>
          ) : null}
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              mt: 1,
              display: 'flex',
              flexWrap: 'wrap',
              columnGap: 1,
              alignItems: 'center',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            <span>{plural(reports.length, 'report')}</span>
            <span aria-hidden>·</span>
            <span>{plural(subspaces.length, 'sub-space')}</span>
            <span aria-hidden>·</span>
            <span>
              Updated {new Date(space.updated_at).toLocaleDateString()} by{' '}
              <UserDisplay userId={space.updated_by ?? space.created_by} />
            </span>
          </Typography>
        </Box>
      </Box>

      {!empty && <ActionBar manage={manage} />}

      {empty ? (
        <EmptySpace name={space.name} manage={manage} />
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 4, mt: 4 }}>
          {reports.length > 0 && !overviewId && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: MEASURE,
                display: 'flex',
                gap: 1,
                alignItems: 'center',
              }}
            >
              <StarIcon fontSize="small" sx={{ color: 'text.disabled' }} />
              No overview yet. Choose “Set as space overview” on a report to
              make it the page this space opens on.
            </Typography>
          )}
          {/* The top level stays a drop target while filing, even empty. */}
          {(topLevel.length > 0 || subspaces.length === 0 || filing) && (
            <TopLevelSection
              title={subspaces.length > 0 ? 'Top level' : 'Reports'}
              reports={topLevel}
              overviewId={overviewId}
              manage={manage}
              reportPath={reportPath}
              selection={selection}
            />
          )}
          {subspaces.length > 0 && (
            <SortableContext
              items={subspaceSortIds('contents', subspaces)}
              strategy={verticalListSortingStrategy}
            >
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {subspaces.map((subspace) => (
                  <SubspacePanel
                    key={subspace.subspace_id}
                    subspace={subspace}
                    reports={reports.filter(
                      (r) => r.subspace_id === subspace.subspace_id,
                    )}
                    overviewId={overviewId}
                    manage={manage}
                    reportPath={reportPath}
                    selection={selection}
                  />
                ))}
              </Box>
            </SortableContext>
          )}
        </Box>
      )}

      {selected.length > 0 && (
        <SelectionBar
          selected={selected}
          subspaces={subspaces}
          manage={manage}
          onClear={clearSelection}
        />
      )}
    </Box>
  );
}

function SelectionBar({
  selected,
  subspaces,
  manage,
  onClear,
}: {
  selected: ReportListItem[];
  subspaces: SubspaceItem[];
  manage: SpaceManagement;
  onClear: () => void;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const targets = [null, ...subspaces];
  const allIn = (subspaceId: string | null) =>
    selected.every((r) => (r.subspace_id ?? null) === subspaceId);

  const move = async (subspaceId: string | null) => {
    setAnchor(null);
    onClear();
    await manage.moveReports(selected, subspaceId);
  };

  return (
    <Paper
      role="region"
      aria-label="Selected reports"
      elevation={8}
      sx={{
        position: 'sticky',
        bottom: 16,
        zIndex: 2,
        mt: 4,
        mx: 'auto',
        width: 'fit-content',
        maxWidth: '100%',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 1,
        pl: 2,
        pr: 1,
        py: 1,
        borderRadius: 2,
        border: 1,
        borderColor: 'primary.main',
        '@keyframes selection-bar-in': {
          from: { transform: 'translateY(12px)', opacity: 0 },
          to: { transform: 'none', opacity: 1 },
        },
        animation: 'selection-bar-in 200ms cubic-bezier(0.16, 1, 0.3, 1)',
        '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
      }}
    >
      <Typography
        variant="body2"
        sx={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums', mr: 1 }}
      >
        {plural(selected.length, 'report')} selected
      </Typography>
      {subspaces.length > 0 && (
        <>
          <Button
            variant="contained"
            size="small"
            startIcon={<DriveFileMoveIcon fontSize="small" />}
            onClick={(e) => setAnchor(e.currentTarget)}
            disabled={manage.moving}
            aria-haspopup="menu"
          >
            Move to…
          </Button>
          <Menu
            anchorEl={anchor}
            open={!!anchor}
            onClose={() => setAnchor(null)}
            anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
            transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          >
            {targets.map((subspace, index) => {
              const id = subspace?.subspace_id ?? null;
              return [
                index === 1 && <Divider key="divider" />,
                <MenuItem
                  key={id ?? 'top'}
                  disabled={allIn(id)}
                  onClick={() => void move(id)}
                >
                  {subspace?.name ?? 'Top level'}
                </MenuItem>,
              ];
            })}
          </Menu>
        </>
      )}
      <Button
        size="small"
        color="inherit"
        startIcon={<RemoveCircleOutlineIcon fontSize="small" />}
        disabled={manage.moving}
        onClick={() => {
          onClear();
          void manage.removeReports(selected);
        }}
      >
        Remove from space
      </Button>
      <Tooltip title="Clear selection">
        <IconButton size="small" aria-label="Clear selection" onClick={onClear}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Paper>
  );
}

function ActionBar({ manage }: { manage: SpaceManagement }) {
  if (!manage.canWriteReports && !manage.canWriteSpaces) return null;
  return (
    <Box
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1,
        pb: 3,
        borderBottom: 1,
        borderColor: 'divider',
      }}
    >
      {manage.canWriteReports && (
        <>
          <Button
            variant="contained"
            startIcon={<LibraryAddOutlinedIcon />}
            onClick={() => manage.openAddReports()}
          >
            Add reports
          </Button>
          <Button
            variant="outlined"
            startIcon={<PostAddOutlinedIcon />}
            onClick={() => manage.openCreateReport()}
          >
            New report
          </Button>
        </>
      )}
      {manage.canWriteSpaces && (
        <Button
          color="inherit"
          startIcon={<CreateNewFolderOutlinedIcon />}
          onClick={manage.openCreateSubspace}
          sx={{ color: 'text.secondary' }}
        >
          New sub-space
        </Button>
      )}
    </Box>
  );
}

function SectionCount({ count }: { count: number }) {
  return (
    <Typography
      variant="body2"
      color="text.secondary"
      sx={{ fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}
    >
      {count}
    </Typography>
  );
}

function DropHint() {
  return (
    <Typography
      variant="body2"
      color="primary"
      sx={{ fontWeight: 600, flexShrink: 0 }}
    >
      Drop to move here
    </Typography>
  );
}

/** Select every report in a section, sitting in the rows' checkbox column. */
function SelectAll({
  reports,
  title,
  selection,
}: {
  reports: ReportListItem[];
  title: string;
  selection: Selection;
}) {
  if (reports.length === 0) {
    // Nothing to select, but the heading keeps its column.
    return <Box aria-hidden sx={{ flexShrink: 0, width: CHECKBOX_WIDTH }} />;
  }
  const picked = reports.filter(selection.has).length;
  return (
    <Checkbox
      size="small"
      checked={picked > 0 && picked === reports.length}
      indeterminate={picked > 0 && picked < reports.length}
      onChange={() => selection.toggleAll(reports)}
      slotProps={{
        input: { 'aria-label': `Select every report in ${title}` },
      }}
      sx={{ p: 0.5, flexShrink: 0 }}
    />
  );
}

function ReportList({
  reports,
  overviewId,
  manage,
  reportPath,
  selection,
  framed,
}: {
  reports: ReportListItem[];
  overviewId: string | null;
  manage: SpaceManagement;
  reportPath: (reportId: string) => string;
  selection: Selection;
  /** Inside a panel: the panel's edge closes the list, not a rule. */
  framed: boolean;
}) {
  return (
    <Box
      component="ul"
      sx={{
        listStyle: 'none',
        m: 0,
        p: 0,
        ...(framed
          ? { '& > li:last-of-type': { borderBottom: 0 } }
          : { borderTop: 1, borderColor: 'divider' }),
      }}
    >
      {reports.map((report) => (
        <ReportRow
          key={report.report_id}
          report={report}
          isOverview={report.report_id === overviewId}
          manage={manage}
          href={reportPath(report.report_id)}
          selection={selection}
        />
      ))}
    </Box>
  );
}

/**
 * The reports filed in no sub-space. Unframed, so they read as the space's
 * loose contents, apart from the sub-space panels below.
 */
function TopLevelSection({
  title,
  reports,
  overviewId,
  manage,
  reportPath,
  selection,
}: {
  title: string;
  reports: ReportListItem[];
  overviewId: string | null;
  manage: SpaceManagement;
  reportPath: (reportId: string) => string;
  selection: Selection;
}) {
  const headingId = 'space-section-top';
  const drop = useSubspaceDrop('contents', null, 'the top level');
  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      ref={drop.setNodeRef}
      sx={[
        dropTargetSx(drop.live, drop.wouldMove, drop.isOver),
        { p: 1, m: -1 },
      ]}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: `${ROW_GAP}px`,
          mb: 1,
          minHeight: 32,
          pl: selection.enabled ? `${ROW_PAD + GRIP_WIDTH + ROW_GAP}px` : 1.5,
        }}
      >
        {selection.enabled && (
          <SelectAll reports={reports} title={title} selection={selection} />
        )}
        <Typography
          id={headingId}
          variant="h5"
          component="h2"
          sx={{ ...listTableTruncateSx, minWidth: 0 }}
        >
          {title}
        </Typography>
        <SectionCount count={reports.length} />
        <Box sx={{ flex: 1 }} />
        {drop.isOver && <DropHint />}
      </Box>

      {reports.length === 0 ? (
        <EmptySection manage={manage} />
      ) : (
        <ReportList
          reports={reports}
          overviewId={overviewId}
          manage={manage}
          reportPath={reportPath}
          selection={selection}
          framed={false}
        />
      )}
    </Box>
  );
}

/**
 * One sub-space: a framed panel with its own header bar, reorderable by the
 * grip at the header's start and a drop target for reports.
 */
function SubspacePanel({
  subspace,
  reports,
  overviewId,
  manage,
  reportPath,
  selection,
}: {
  subspace: SubspaceItem;
  reports: ReportListItem[];
  overviewId: string | null;
  manage: SpaceManagement;
  reportPath: (reportId: string) => string;
  selection: Selection;
}) {
  const headingId = `space-section-${subspace.subspace_id}`;
  const drop = useSubspaceDrop('contents', subspace.subspace_id, subspace.name);
  const canReorder = manage.canWriteSpaces;
  const sort = useSubspaceSort(
    'contents',
    subspace,
    !canReorder || manage.moving,
  );
  // One element is both the reports' drop target and the sub-space's slot.
  const setRef = (node: HTMLElement | null) => {
    drop.setNodeRef(node);
    sort.setNodeRef(node);
  };
  const leadPad = canReorder || selection.enabled;

  return (
    <Paper
      variant="outlined"
      component="section"
      aria-labelledby={headingId}
      ref={setRef}
      style={sort.style}
      sx={[
        dropTargetSx(drop.live, drop.wouldMove, drop.isOver),
        (theme) => ({
          // After the drop-target look, which would otherwise square it off.
          borderRadius: 1.5,
          position: 'relative',
          overflow: 'clip',
          // The header band is the panel's lid: a clear step above the rows
          // in both themes.
          '& > .subspace-header': {
            bgcolor: alpha(
              theme.palette.primary.main,
              dark(theme) ? 0.12 : 0.07,
            ),
            borderColor: alpha(
              theme.palette.primary.main,
              dark(theme) ? 0.25 : 0.18,
            ),
          },
          // Lifted while it travels, above the panels sliding aside under it.
          ...(sort.isDragging && {
            zIndex: 2,
            borderColor: 'primary.main',
            boxShadow: `0 16px 36px ${alpha(theme.palette.common.black, dark(theme) ? 0.5 : 0.22)}`,
            cursor: 'grabbing',
          }),
          '& .subspace-grip': { opacity: 0.55 },
          '&:hover .subspace-grip, & .subspace-grip:focus-visible': {
            opacity: 1,
          },
        }),
      ]}
    >
      <Box
        className="subspace-header"
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: `${ROW_GAP}px`,
          minHeight: 48,
          pl: leadPad ? `${ROW_PAD}px` : 1.5,
          pr: 0.5,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        {canReorder ? (
          <Tooltip title="Drag to reorder" placement="top" disableInteractive>
            <IconButton
              ref={sort.setActivatorNodeRef}
              className="subspace-grip"
              size="small"
              aria-label={`Reorder ${subspace.name}`}
              {...sort.attributes}
              {...sort.listeners}
              sx={{
                cursor: 'grab',
                transition: 'opacity 150ms ease-out',
                touchAction: 'none',
                width: GRIP_WIDTH,
                flexShrink: 0,
                p: 0,
              }}
            >
              <DragIndicatorIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        ) : (
          selection.enabled && (
            <Box aria-hidden sx={{ width: GRIP_WIDTH, flexShrink: 0 }} />
          )
        )}
        {selection.enabled && (
          <SelectAll
            reports={reports}
            title={subspace.name}
            selection={selection}
          />
        )}
        <Typography
          id={headingId}
          variant="h5"
          component="h2"
          sx={{ minWidth: 0, overflowWrap: 'anywhere', py: 0.5 }}
        >
          {subspace.name}
        </Typography>
        <SectionCount count={reports.length} />
        <Box sx={{ flex: 1 }} />
        {drop.isOver && <DropHint />}
        {manage.canWriteReports && reports.length > 0 && (
          <Button
            size="small"
            startIcon={<LibraryAddOutlinedIcon fontSize="small" />}
            onClick={() => manage.openAddReports(subspace.subspace_id)}
            // On a phone the name needs the room; the menu offers it too.
            sx={{ flexShrink: 0, display: { xs: 'none', sm: 'inline-flex' } }}
          >
            Add
          </Button>
        )}
        {(manage.canWriteSpaces || manage.canWriteReports) && (
          <RowMenu
            actions={manage.subspaceActions(subspace)}
            label={`${subspace.name} actions`}
            menuMinWidth={200}
          />
        )}
      </Box>

      {reports.length === 0 ? (
        <EmptyPanel subspace={subspace} manage={manage} />
      ) : (
        <ReportList
          reports={reports}
          overviewId={overviewId}
          manage={manage}
          reportPath={reportPath}
          selection={selection}
          framed
        />
      )}
    </Paper>
  );
}

function ReportRow({
  report,
  isOverview,
  manage,
  href,
  selection,
}: {
  report: ReportListItem;
  isOverview: boolean;
  manage: SpaceManagement;
  href: string;
  selection: Selection;
}) {
  const checked = selection.has(report);
  const drag = useReportDrag(
    'contents',
    report,
    selection.carried(report),
    !selection.enabled || manage.moving,
  );
  const lead = selection.enabled ? 'auto auto ' : '';
  return (
    <Box
      component="li"
      ref={drag.setNodeRef}
      sx={(theme) => ({
        display: 'grid',
        gridTemplateColumns: {
          xs: `${lead}minmax(0, 1fr) auto`,
          md: `${lead}minmax(0, 1fr) 160px 160px auto`,
        },
        alignItems: 'center',
        columnGap: selection.enabled ? `${ROW_GAP}px` : 2,
        minHeight: 48,
        pl: selection.enabled ? `${ROW_PAD}px` : 1.5,
        pr: 0.5,
        borderBottom: 1,
        borderColor: 'divider',
        position: 'relative',
        opacity: drag.isDragging ? 0.4 : 1,
        transition: 'background-color 150ms ease-out, opacity 150ms ease-out',
        bgcolor: checked ? alpha(theme.palette.primary.main, 0.1) : undefined,
        '&:hover, &:focus-within': {
          bgcolor: alpha(theme.palette.primary.main, checked ? 0.14 : 0.06),
        },
        '& .drag-handle': { opacity: 0.45 },
        '&:hover .drag-handle, & .drag-handle:focus-visible': { opacity: 1 },
      })}
    >
      {selection.enabled && (
        <>
          <IconButton
            ref={drag.setActivatorNodeRef}
            className="drag-handle"
            size="small"
            aria-label={`Move ${report.name}`}
            {...drag.attributes}
            {...drag.listeners}
            sx={{
              position: 'relative',
              zIndex: 1,
              cursor: 'grab',
              transition: 'opacity 150ms ease-out',
              touchAction: 'none',
              width: GRIP_WIDTH,
              p: 0,
            }}
          >
            <DragIndicatorIcon fontSize="small" />
          </IconButton>
          <Checkbox
            size="small"
            checked={checked}
            onChange={() => selection.toggle(report)}
            slotProps={{ input: { 'aria-label': `Select ${report.name}` } }}
            sx={{ position: 'relative', zIndex: 1, p: 0.5 }}
          />
        </>
      )}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
        <Link
          component={RouterLink}
          to={href}
          underline="hover"
          color="text.primary"
          sx={{
            ...listTableTruncateSx,
            fontWeight: 500,
            // The whole row is the target, the menu excepted.
            '&::after': { content: '""', position: 'absolute', inset: 0 },
          }}
        >
          {report.name}
        </Link>
        {isOverview && (
          <Box
            component="span"
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 0.5,
              flexShrink: 0,
              color: 'secondary.main',
              typography: 'caption',
              fontWeight: 600,
            }}
          >
            <StarIcon sx={{ fontSize: 14 }} titleAccess="Space overview" />
            Overview
          </Box>
        )}
      </Box>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{
          display: { xs: 'none', md: 'block' },
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {new Date(report.updated_at).toLocaleDateString()}
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ ...listTableTruncateSx, display: { xs: 'none', md: 'block' } }}
      >
        <UserDisplay userId={report.updated_by ?? report.created_by} />
      </Typography>
      <Box sx={{ position: 'relative', zIndex: 1, minWidth: 30 }}>
        {(manage.canWriteReports || manage.canWriteSpaces) && (
          <RowMenu
            actions={manage.reportActions(report)}
            label="Report actions"
            menuMinWidth={200}
          />
        )}
      </Box>
    </Box>
  );
}

function EmptyActions({
  subspaceId,
  manage,
}: {
  subspaceId: string | null;
  manage: SpaceManagement;
}) {
  if (!manage.canWriteReports) return null;
  return (
    <Box sx={{ display: 'flex', gap: 1 }}>
      <Button
        size="small"
        variant="outlined"
        startIcon={<LibraryAddOutlinedIcon fontSize="small" />}
        onClick={() => manage.openAddReports(subspaceId)}
      >
        Add reports
      </Button>
      <Button
        size="small"
        startIcon={<PostAddOutlinedIcon fontSize="small" />}
        onClick={() => manage.openCreateReport(subspaceId)}
      >
        New report
      </Button>
    </Box>
  );
}

/** The empty top level, shown only as a target while a drag is live. */
function EmptySection({ manage }: { manage: SpaceManagement }) {
  return (
    <Box
      sx={(theme) => ({
        border: `1px dashed ${alpha(theme.palette.primary.main, 0.35)}`,
        borderRadius: 1,
        px: 2,
        py: 2,
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 1.5,
      })}
    >
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ flex: 1, minWidth: 200 }}
      >
        No reports at the top level.
      </Typography>
      <EmptyActions subspaceId={null} manage={manage} />
    </Box>
  );
}

/** An empty sub-space's body: the panel already frames it. */
function EmptyPanel({
  subspace,
  manage,
}: {
  subspace: SubspaceItem;
  manage: SpaceManagement;
}) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 1.5,
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 1.5,
      }}
    >
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ flex: 1, minWidth: 200 }}
      >
        Nothing in {subspace.name} yet.
      </Typography>
      <EmptyActions subspaceId={subspace.subspace_id} manage={manage} />
    </Box>
  );
}

function EmptySpace({
  name,
  manage,
}: {
  name: string;
  manage: SpaceManagement;
}) {
  return (
    <Box
      sx={(theme) => ({
        mt: 1,
        px: { xs: 3, sm: 5 },
        py: { xs: 4, sm: 6 },
        borderRadius: 2,
        border: `1px dashed ${alpha(theme.palette.primary.main, 0.35)}`,
        bgcolor: alpha(theme.palette.primary.main, 0.04),
      })}
    >
      <Typography variant="h3" component="h2" sx={{ mb: 1 }}>
        Chart what goes in {name}
      </Typography>
      <Typography color="text.secondary" sx={{ maxWidth: MEASURE, mb: 3 }}>
        Bring in reports you already have, or start a new one here. Group them
        into sub-spaces as the space grows, and pick one as the overview it
        opens on. Reports in a space are visible to everyone.
      </Typography>
      {manage.canWriteReports || manage.canWriteSpaces ? (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          {manage.canWriteReports && (
            <>
              <Button
                variant="contained"
                size="large"
                startIcon={<LibraryAddOutlinedIcon />}
                onClick={() => manage.openAddReports()}
              >
                Add existing reports
              </Button>
              <Button
                variant="outlined"
                size="large"
                startIcon={<PostAddOutlinedIcon />}
                onClick={() => manage.openCreateReport()}
              >
                New report
              </Button>
            </>
          )}
          {manage.canWriteSpaces && (
            <Button
              size="large"
              color="inherit"
              startIcon={<CreateNewFolderOutlinedIcon />}
              onClick={manage.openCreateSubspace}
              sx={{ color: 'text.secondary' }}
            >
              New sub-space
            </Button>
          )}
        </Box>
      ) : (
        <Typography variant="body2" color="text.secondary">
          You can view this space but not add to it.
        </Typography>
      )}
    </Box>
  );
}
