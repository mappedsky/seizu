import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  List,
  ListItem,
  ListItemButton,
  MenuItem,
  Select,
  Snackbar,
  TextField,
  Typography,
} from '@mui/material';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import DeleteIcon from '@mui/icons-material/Delete';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import DriveFileMoveOutlinedIcon from '@mui/icons-material/DriveFileMoveOutlined';
import EditIcon from '@mui/icons-material/Edit';
import LibraryAddOutlinedIcon from '@mui/icons-material/LibraryAddOutlined';
import PostAddOutlinedIcon from '@mui/icons-material/PostAddOutlined';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircleOutlined';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import MoveToSpaceDialog, {
  type MovePlacementNames,
} from 'src/components/MoveToSpaceDialog';
import type { RowMenuAction } from 'src/components/RowMenu';
import AddReportsDrawer from 'src/components/spaces/AddReportsDrawer';
import SpaceDialog from 'src/components/spaces/SpaceDialog';
import {
  notifyReportsUpdated,
  useReportsMutations,
  type ReportListItem,
} from 'src/hooks/useReportsApi';
import { useBulkAction } from 'src/hooks/useBulkAction';
import {
  useSpaceMutations,
  useSubspaceMutations,
  type SpaceTree,
  type SubspaceItem,
} from 'src/hooks/useSpacesApi';
import { usePermissions } from 'src/hooks/usePermissions';

const TOP_LEVEL = '__top__';
/** Up to this many sub-spaces, a report's menu lists them as move targets. */
const INLINE_MOVE_TARGETS = 6;

const reportKey = (report: ReportListItem) => report.report_id;

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

export interface SpaceManagement {
  /**
   * The tree as it should be drawn: the fetched tree with in-flight moves
   * applied, so a moved report shows in its new place before the reload.
   */
  view: SpaceTree | null;
  /** File reports into a sub-space (null: top level), one reload at the end. */
  moveReports: (
    reports: ReportListItem[],
    subspaceId: string | null,
  ) => Promise<void>;
  /** Take reports out of the space, one reload at the end. */
  removeReports: (reports: ReportListItem[]) => Promise<void>;
  /** Pick another space (and sub-space) for these reports, then move them. */
  openMoveToSpace: (reports: ReportListItem[]) => void;
  /** A batch move or removal is running. */
  moving: boolean;
  /**
   * Put a sub-space where another one is, shifting those between. Drawn at
   * once; reverted with an error if the save fails.
   */
  reorderSubspace: (subspaceId: string, overSubspaceId: string) => void;
  /** spaces:write — sub-spaces, the overview pointer, the space itself. */
  canWriteSpaces: boolean;
  /** spaces:delete — deleting sub-spaces. */
  canDeleteSpaces: boolean;
  /** reports:write — creating reports and filing them in or out. */
  canWriteReports: boolean;
  reportActions: (report: ReportListItem) => RowMenuAction[];
  subspaceActions: (subspace: SubspaceItem) => RowMenuAction[];
  openAddReports: (subspaceId?: string | null) => void;
  openCreateReport: (subspaceId?: string | null) => void;
  openCreateSubspace: () => void;
  openEditSpace: () => void;
  /** Every dialog, drawer and toast the actions above open. Render once. */
  dialogs: ReactNode;
}

function messageOf(err: unknown, fallback: string): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (err as any)?.message ?? fallback;
}

/**
 * Everything a space page can do to the space, in one place, so the sidebar
 * and the contents page offer identical actions with identical gating.
 *
 * Each permission gates only what it governs: a role holding spaces:write but
 * not reports:write can pin an overview but cannot file a report.
 */
export function useSpaceManagement(
  tree: SpaceTree | null,
  /** The report on screen, if any, so removing it can leave the route. */
  openReportId: string | undefined,
): SpaceManagement {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const spaceId = tree?.space.space_id ?? '';
  const { createReport, setReportSpace } = useReportsMutations();
  const { setSpaceOverview, updateSpace } = useSpaceMutations();
  const { createSubspace, updateSubspace, deleteSubspace, reorderSubspaces } =
    useSubspaceMutations(spaceId);

  const canWriteSpaces = hasPermission('spaces:write');
  const canDeleteSpaces = hasPermission('spaces:delete');
  const canWriteReports = hasPermission('reports:write');

  const [toastState, setToastState] = useState<{
    message: string;
    /** Where the toast's “Open” action goes, when the change lands elsewhere. */
    href?: string;
  } | null>(null);
  const setToast = (message: string | null, href?: string) =>
    setToastState(message === null ? null : { message, href });
  const [actionError, setActionError] = useState<string | null>(null);

  // Add existing reports
  const [addOpen, setAddOpen] = useState(false);
  const [addTarget, setAddTarget] = useState<string | null>(null);

  // New report
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createTarget, setCreateTarget] = useState<string>(TOP_LEVEL);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Sub-space create / rename
  const [subspaceDialogOpen, setSubspaceDialogOpen] = useState(false);
  const [subspaceEditTarget, setSubspaceEditTarget] =
    useState<SubspaceItem | null>(null);
  const [subspaceName, setSubspaceName] = useState('');
  const [savingSubspace, setSavingSubspace] = useState(false);
  const [subspaceError, setSubspaceError] = useState<string | null>(null);

  // Sub-space delete
  const [deleteTarget, setDeleteTarget] = useState<SubspaceItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Move within the space
  const [moveTarget, setMoveTarget] = useState<ReportListItem | null>(null);
  // Move to another space
  const [spaceMoveTargets, setSpaceMoveTargets] = useState<
    ReportListItem[] | null
  >(null);

  // Edit space
  const [editOpen, setEditOpen] = useState(false);

  // Optimistic placement. Pinned to the tree it was made against: when the
  // tree is refetched after the batch, the overrides stop applying and the
  // server's answer is drawn instead, with no effect needed to clear them.
  const [pending, setPending] = useState<{
    base: SpaceTree | null;
    moves: Record<string, string | null>;
    removed: string[];
  }>({ base: null, moves: {}, removed: [] });
  const bulk = useBulkAction<ReportListItem>(reportKey);
  // Optimistic sub-space order, pinned to its tree the same way.
  const [pendingOrder, setPendingOrder] = useState<{
    base: SpaceTree | null;
    ids: string[];
  }>({ base: null, ids: [] });

  const view = useMemo<SpaceTree | null>(() => {
    if (!tree) return tree;
    const placed = pending.base === tree;
    const ordered = pendingOrder.base === tree;
    if (!placed && !ordered) return tree;
    const removed = new Set(placed ? pending.removed : []);
    const rank = new Map(pendingOrder.ids.map((id, index) => [id, index]));
    const overview = tree.space.overview_report_id;
    return {
      ...tree,
      // The server blanks a pointer to a report that has left; so does the
      // drawn tree, or the space root would briefly say the overview is gone.
      space:
        overview && removed.has(overview)
          ? { ...tree.space, overview_report_id: null }
          : tree.space,
      subspaces: ordered
        ? [...tree.subspaces].sort(
            (a, b) =>
              (rank.get(a.subspace_id) ?? Infinity) -
              (rank.get(b.subspace_id) ?? Infinity),
          )
        : tree.subspaces,
      reports: placed
        ? tree.reports
            .filter((r) => !removed.has(r.report_id))
            .map((r) =>
              r.report_id in pending.moves
                ? { ...r, subspace_id: pending.moves[r.report_id] }
                : r,
            )
        : tree.reports,
    };
  }, [tree, pending, pendingOrder]);

  const subspaceLabel = (subspaceId: string | null) =>
    subspaceId === null
      ? 'the top level'
      : (tree?.subspaces.find((s) => s.subspace_id === subspaceId)?.name ??
        'the sub-space');

  const reportPath = useCallback(
    (reportId: string) => `/app/spaces/${spaceId}/reports/${reportId}`,
    [spaceId],
  );

  const openAddReports = useCallback((subspaceId: string | null = null) => {
    setAddTarget(subspaceId);
    setAddOpen(true);
  }, []);

  const openCreateReport = useCallback((subspaceId: string | null = null) => {
    setCreateName('');
    setCreateError(null);
    setCreateTarget(subspaceId ?? TOP_LEVEL);
    setCreateOpen(true);
  }, []);

  const openCreateSubspace = useCallback(() => {
    setSubspaceEditTarget(null);
    setSubspaceName('');
    setSubspaceError(null);
    setSubspaceDialogOpen(true);
  }, []);

  const openEditSpace = useCallback(() => setEditOpen(true), []);

  const run = async (label: string, action: () => Promise<unknown>) => {
    try {
      await action();
    } catch (err) {
      setActionError(`${label}: ${messageOf(err, 'failed')}`);
    }
  };

  const reportFailures = (
    failed: { item: ReportListItem; message: string }[],
  ) =>
    failed.map(({ item, message }) => `“${item.name}”: ${message}`).join('; ');

  const moveReports = async (
    reports: ReportListItem[],
    subspaceId: string | null,
  ) => {
    const moving = reports.filter(
      (r) => (r.subspace_id ?? null) !== subspaceId,
    );
    if (!tree || moving.length === 0) return;
    setPending((prev) => ({
      base: tree,
      moves: {
        ...(prev.base === tree ? prev.moves : {}),
        ...Object.fromEntries(moving.map((r) => [r.report_id, subspaceId])),
      },
      removed: prev.base === tree ? prev.removed : [],
    }));
    const { succeeded, failed } = await bulk.run(
      moving,
      (report) =>
        setReportSpace(report.report_id, spaceId, subspaceId, {
          notify: false,
        }),
      notifyReportsUpdated,
    );
    if (failed.length > 0) {
      // Put the failures back where they were until the reload lands.
      setPending((prev) => {
        const moves = { ...prev.moves };
        failed.forEach(({ item }) => delete moves[item.report_id]);
        return { ...prev, moves };
      });
      setActionError(
        `${plural(failed.length, 'report')} could not be moved. ${reportFailures(failed)}`,
      );
    }
    if (succeeded.length > 0) {
      setToast(
        succeeded.length === 1
          ? `Moved “${succeeded[0].name}” to ${subspaceLabel(subspaceId)}`
          : `Moved ${succeeded.length} reports to ${subspaceLabel(subspaceId)}`,
      );
    }
  };

  const removeReports = async (reports: ReportListItem[]) => {
    if (!tree || reports.length === 0) return;
    setPending((prev) => ({
      base: tree,
      moves: prev.base === tree ? prev.moves : {},
      removed: [
        ...(prev.base === tree ? prev.removed : []),
        ...reports.map((r) => r.report_id),
      ],
    }));
    const { succeeded, failed } = await bulk.run(
      reports,
      (report) =>
        setReportSpace(report.report_id, null, null, { notify: false }),
      notifyReportsUpdated,
    );
    if (failed.length > 0) {
      setPending((prev) => ({
        ...prev,
        removed: prev.removed.filter(
          (id) => !failed.some(({ item }) => item.report_id === id),
        ),
      }));
      setActionError(
        `${plural(failed.length, 'report')} could not be removed. ${reportFailures(failed)}`,
      );
    }
    if (succeeded.length > 0) {
      setToast(
        succeeded.length === 1
          ? `Removed “${succeeded[0].name}” from ${tree.space.name}`
          : `Removed ${succeeded.length} reports from ${tree.space.name}`,
      );
    }
    // Removing the report on screen falls back to the space root rather than
    // leaving a report the space no longer contains.
    if (succeeded.some((r) => r.report_id === openReportId)) {
      navigate(`/app/spaces/${spaceId}`, { replace: true });
    }
  };

  const moveToSpace = async (
    reports: ReportListItem[],
    destSpaceId: string,
    destSubspaceId: string | null,
    names: MovePlacementNames,
  ) => {
    if (!tree || reports.length === 0 || destSpaceId === spaceId) return;
    // Gone from this space as soon as the move starts, like a removal.
    setPending((prev) => ({
      base: tree,
      moves: prev.base === tree ? prev.moves : {},
      removed: [
        ...(prev.base === tree ? prev.removed : []),
        ...reports.map((r) => r.report_id),
      ],
    }));
    const { succeeded, failed } = await bulk.run(
      reports,
      (report) =>
        setReportSpace(report.report_id, destSpaceId, destSubspaceId, {
          notify: false,
        }),
      notifyReportsUpdated,
    );
    if (failed.length > 0) {
      setPending((prev) => ({
        ...prev,
        removed: prev.removed.filter(
          (id) => !failed.some(({ item }) => item.report_id === id),
        ),
      }));
      setActionError(
        `${plural(failed.length, 'report')} could not be moved. ${reportFailures(failed)}`,
      );
    }
    const destination = [names.spaceName ?? 'the space', names.subspaceName]
      .filter(Boolean)
      .join(' › ');
    if (succeeded.length > 0) {
      setToast(
        succeeded.length === 1
          ? `Moved “${succeeded[0].name}” to ${destination}`
          : `Moved ${succeeded.length} reports to ${destination}`,
        `/app/spaces/${destSpaceId}/contents`,
      );
    }
    // The report on screen goes with its move: follow it to its new space.
    if (openReportId && succeeded.some((r) => r.report_id === openReportId)) {
      navigate(`/app/spaces/${destSpaceId}/reports/${openReportId}`, {
        replace: true,
      });
    }
  };

  const openMoveToSpace = useCallback((reports: ReportListItem[]) => {
    if (reports.length > 0) setSpaceMoveTargets(reports);
  }, []);

  const reorderSubspace = (subspaceId: string, overSubspaceId: string) => {
    if (!tree || !view) return;
    const ids = view.subspaces.map((s) => s.subspace_id);
    const from = ids.indexOf(subspaceId);
    const to = ids.indexOf(overSubspaceId);
    if (from === -1 || to === -1 || from === to) return;
    ids.splice(to, 0, ...ids.splice(from, 1));
    setPendingOrder({ base: tree, ids });
    // The full list every time, so overlapping saves agree on the last one.
    reorderSubspaces(ids).catch((err) => {
      setPendingOrder((prev) =>
        prev.ids === ids ? { base: null, ids: [] } : prev,
      );
      setActionError(
        `Could not reorder sub-spaces: ${messageOf(err, 'failed')}`,
      );
    });
  };

  const handleCreateReport = async () => {
    if (!createName.trim() || !spaceId) return;
    setCreating(true);
    setCreateError(null);
    try {
      const item = await createReport(
        createName.trim(),
        spaceId,
        createTarget === TOP_LEVEL ? null : createTarget,
      );
      setCreateOpen(false);
      navigate(`${reportPath(item.report_id)}?edit=true`);
    } catch (err) {
      setCreateError(messageOf(err, 'Failed to create report'));
    } finally {
      setCreating(false);
    }
  };

  const handleSaveSubspace = async () => {
    const trimmed = subspaceName.trim();
    if (!trimmed) {
      setSubspaceError('Name is required.');
      return;
    }
    setSavingSubspace(true);
    setSubspaceError(null);
    try {
      if (subspaceEditTarget) {
        await updateSubspace(subspaceEditTarget.subspace_id, trimmed);
      } else {
        await createSubspace(trimmed);
      }
      setSubspaceDialogOpen(false);
    } catch (err) {
      setSubspaceError(messageOf(err, 'Failed to save sub-space.'));
    } finally {
      setSavingSubspace(false);
    }
  };

  const handleDeleteSubspace = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteSubspace(deleteTarget.subspace_id);
      setDeleteTarget(null);
    } catch (err) {
      setDeleteError(messageOf(err, 'Failed to delete sub-space.'));
    } finally {
      setDeleting(false);
    }
  };

  const moveActions = (report: ReportListItem): RowMenuAction[] => {
    const subspaces = tree?.subspaces ?? [];
    const here = report.subspace_id ?? null;
    const blocked = !canWriteReports
      ? 'You do not have permission to move reports'
      : undefined;
    if (subspaces.length === 0) return [];
    if (subspaces.length > INLINE_MOVE_TARGETS) {
      return [
        {
          key: 'move',
          label: 'Move to sub-space…',
          icon: <DriveFileMoveIcon fontSize="small" />,
          onClick: () => setMoveTarget(report),
          disabled: !canWriteReports,
          tooltip: blocked,
          dividerBefore: true,
        },
      ];
    }
    // Few enough to name: one click per move, no dialog.
    return [null, ...subspaces]
      .filter((subspace) => (subspace?.subspace_id ?? null) !== here)
      .map((subspace, index) => ({
        key: `move:${subspace?.subspace_id ?? TOP_LEVEL}`,
        label: `Move to ${subspace?.name ?? 'top level'}`,
        icon: <DriveFileMoveIcon fontSize="small" />,
        onClick: () =>
          void moveReports([report], subspace?.subspace_id ?? null),
        // One batch at a time: a second would race the first's reload.
        disabled: !canWriteReports || bulk.running,
        tooltip: blocked,
        dividerBefore: index === 0,
      }));
  };

  const reportActions = (report: ReportListItem): RowMenuAction[] => {
    const isOverview = report.report_id === tree?.space.overview_report_id;
    return [
      {
        key: 'overview',
        label: isOverview ? 'Clear space overview' : 'Set as space overview',
        icon: isOverview ? (
          <StarIcon fontSize="small" color="secondary" />
        ) : (
          <StarBorderIcon fontSize="small" />
        ),
        onClick: () =>
          void run('Could not change the overview', () =>
            setSpaceOverview(spaceId, isOverview ? null : report.report_id),
          ),
        disabled: !canWriteSpaces,
        tooltip: canWriteSpaces
          ? undefined
          : 'You do not have permission to change the space overview',
      },
      ...moveActions(report),
      {
        key: 'move-space',
        label: 'Move to another space…',
        icon: <DriveFileMoveOutlinedIcon fontSize="small" />,
        onClick: () => openMoveToSpace([report]),
        disabled: !canWriteReports || bulk.running,
        tooltip: canWriteReports
          ? undefined
          : 'You do not have permission to move reports',
        dividerBefore: (tree?.subspaces.length ?? 0) === 0,
      },
      {
        key: 'remove',
        label: 'Remove from space',
        icon: <RemoveCircleOutlineIcon fontSize="small" />,
        onClick: () => void removeReports([report]),
        disabled: !canWriteReports,
        tooltip: canWriteReports
          ? undefined
          : 'You do not have permission to move reports',
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const orderActions = (subspace: SubspaceItem): RowMenuAction[] => {
    const order = view?.subspaces ?? [];
    if (order.length < 2) return [];
    const index = order.findIndex(
      (s) => s.subspace_id === subspace.subspace_id,
    );
    const blocked = canWriteSpaces
      ? undefined
      : 'You do not have permission to reorder sub-spaces';
    return [
      {
        key: 'up',
        label: 'Move up',
        icon: <ArrowUpwardIcon fontSize="small" />,
        onClick: () =>
          reorderSubspace(subspace.subspace_id, order[index - 1].subspace_id),
        disabled: !canWriteSpaces || index <= 0,
        tooltip: blocked,
        dividerBefore: true,
      },
      {
        key: 'down',
        label: 'Move down',
        icon: <ArrowDownwardIcon fontSize="small" />,
        onClick: () =>
          reorderSubspace(subspace.subspace_id, order[index + 1].subspace_id),
        disabled: !canWriteSpaces || index >= order.length - 1,
        tooltip: blocked,
      },
    ];
  };

  const subspaceActions = (subspace: SubspaceItem): RowMenuAction[] => [
    {
      key: 'add',
      label: 'Add reports here…',
      icon: <LibraryAddOutlinedIcon fontSize="small" />,
      onClick: () => openAddReports(subspace.subspace_id),
      disabled: !canWriteReports,
      tooltip: canWriteReports
        ? undefined
        : 'You do not have permission to move reports',
    },
    {
      key: 'new',
      label: 'New report here…',
      icon: <PostAddOutlinedIcon fontSize="small" />,
      onClick: () => openCreateReport(subspace.subspace_id),
      disabled: !canWriteReports,
      tooltip: canWriteReports
        ? undefined
        : 'You do not have permission to create reports',
    },
    {
      key: 'rename',
      label: 'Rename',
      icon: <EditIcon fontSize="small" />,
      onClick: () => {
        setSubspaceEditTarget(subspace);
        setSubspaceName(subspace.name);
        setSubspaceError(null);
        setSubspaceDialogOpen(true);
      },
      disabled: !canWriteSpaces,
      dividerBefore: true,
    },
    ...orderActions(subspace),
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteTarget(subspace);
      },
      disabled: !canDeleteSpaces,
      destructive: true,
      dividerBefore: true,
    },
  ];

  const dialogs = tree && (
    <>
      <AddReportsDrawer
        open={addOpen}
        onClose={() => setAddOpen(false)}
        tree={tree}
        initialSubspaceId={addTarget}
        onAdded={(count, subspaceName) =>
          setToast(
            `Added ${count} report${count === 1 ? '' : 's'} to ${
              subspaceName ?? tree.space.name
            }`,
          )
        }
        onCreateReport={
          canWriteReports
            ? () => {
                setAddOpen(false);
                openCreateReport(addTarget);
              }
            : undefined
        }
      />

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>New report in {tree.space.name}</DialogTitle>
        <DialogContent>
          {createError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {createError}
            </Alert>
          )}
          <TextField
            autoFocus
            fullWidth
            label="Report name"
            value={createName}
            onChange={(e) => setCreateName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreateReport()}
            sx={{ mt: 1 }}
          />
          {tree.subspaces.length > 0 && (
            <FormControl fullWidth sx={{ mt: 2 }}>
              <InputLabel id="new-report-subspace">Sub-space</InputLabel>
              <Select
                labelId="new-report-subspace"
                label="Sub-space"
                value={createTarget}
                onChange={(e) => setCreateTarget(e.target.value)}
              >
                <MenuItem value={TOP_LEVEL}>Top level</MenuItem>
                {tree.subspaces.map((subspace) => (
                  <MenuItem
                    key={subspace.subspace_id}
                    value={subspace.subspace_id}
                  >
                    {subspace.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          {/* Not a draft, unlike New report elsewhere: reports in a space are
              public, so the create publishes. */}
          <Alert severity="info" sx={{ mt: 2 }}>
            Reports in a space are visible to everyone, so this one is created
            published.
          </Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)} disabled={creating}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleCreateReport}
            disabled={creating || !createName.trim()}
          >
            {creating ? <ConstellationSpinner size={20} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={subspaceDialogOpen}
        onClose={() => setSubspaceDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {subspaceEditTarget ? 'Rename sub-space' : 'New sub-space'}
        </DialogTitle>
        <DialogContent>
          {subspaceError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {subspaceError}
            </Alert>
          )}
          <TextField
            autoFocus
            fullWidth
            label="Name"
            value={subspaceName}
            onChange={(e) => setSubspaceName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSaveSubspace()}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setSubspaceDialogOpen(false)}
            disabled={savingSubspace}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleSaveSubspace}
            disabled={savingSubspace}
          >
            {savingSubspace ? <ConstellationSpinner size={20} /> : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={!!moveTarget}
        onClose={() => setMoveTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Move to sub-space</DialogTitle>
        <DialogContent>
          <List dense>
            {[null, ...tree.subspaces].map((subspace) => {
              const id = subspace?.subspace_id ?? null;
              return (
                <ListItem key={id ?? TOP_LEVEL} disablePadding>
                  <ListItemButton
                    selected={(moveTarget?.subspace_id ?? null) === id}
                    onClick={async () => {
                      if (!moveTarget) return;
                      const target = moveTarget;
                      setMoveTarget(null);
                      await moveReports([target], id);
                    }}
                  >
                    <Typography variant="body2">
                      {subspace?.name ?? 'Top level'}
                    </Typography>
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMoveTarget(null)}>Cancel</Button>
        </DialogActions>
      </Dialog>

      {spaceMoveTargets && (
        <MoveToSpaceDialog
          open
          reportName={spaceMoveTargets[0].name}
          title={
            spaceMoveTargets.length === 1
              ? `Move “${spaceMoveTargets[0].name}” to another space`
              : `Move ${spaceMoveTargets.length} reports to another space`
          }
          currentSpaceId={null}
          currentSubspaceId={null}
          excludeSpaceId={spaceId}
          onClose={() => setSpaceMoveTargets(null)}
          onConfirm={async (destSpaceId, destSubspaceId, names) => {
            if (!destSpaceId) return;
            const reports = spaceMoveTargets;
            setSpaceMoveTargets(null);
            // Failures are reported per report by the batch, not in the dialog.
            await moveToSpace(reports, destSpaceId, destSubspaceId, names);
          }}
        />
      )}

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete sub-space?"
        deleting={deleting}
        error={deleteError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteSubspace}
      >
        Delete <strong>{deleteTarget?.name}</strong>? Its reports stay in this
        space, at the top level.
      </ConfirmDeleteDialog>

      <SpaceDialog
        key={editOpen ? `edit-${spaceId}` : 'closed'}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        initial={tree.space}
        onSave={async (name, description) => {
          await updateSpace(spaceId, name, description);
        }}
      />

      <Snackbar
        open={!!toastState}
        autoHideDuration={toastState?.href ? 6000 : 4000}
        onClose={() => setToast(null)}
        message={toastState?.message}
        action={
          toastState?.href && (
            <Button
              color="primary"
              size="small"
              onClick={() => {
                const href = toastState.href!;
                setToast(null);
                navigate(href);
              }}
            >
              Open space
            </Button>
          )
        }
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      />
      <Snackbar
        open={!!actionError}
        autoHideDuration={6000}
        onClose={() => setActionError(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          severity="error"
          variant="filled"
          onClose={() => setActionError(null)}
        >
          {actionError}
        </Alert>
      </Snackbar>
    </>
  );

  return {
    view,
    moveReports,
    removeReports,
    openMoveToSpace,
    moving: bulk.running,
    reorderSubspace,
    canWriteSpaces,
    canDeleteSpaces,
    canWriteReports,
    reportActions,
    subspaceActions,
    openAddReports,
    openCreateReport,
    openCreateSubspace,
    openEditSpace,
    dialogs,
  };
}
