import { useMemo, useState } from 'react';
import {
  Link as RouterLink,
  useNavigate,
  useSearchParams,
} from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Link,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import Add from '@mui/icons-material/Add';
import BadgeIcon from '@mui/icons-material/Badge';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import DashboardIcon from '@mui/icons-material/Dashboard';
import DashboardOutlinedIcon from '@mui/icons-material/DashboardOutlined';
import LockIcon from '@mui/icons-material/Lock';
import PushPinIcon from '@mui/icons-material/PushPin';
import PushPinOutlinedIcon from '@mui/icons-material/PushPinOutlined';
import PublicIcon from '@mui/icons-material/Public';
import DraftsIcon from '@mui/icons-material/Drafts';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import WorkspacesIcon from '@mui/icons-material/Workspaces';

import {
  ReportListItem,
  useDashboardReportId,
  useReportsList,
  useReportsMutations,
} from 'src/hooks/useReportsApi';
import { useSpacesList } from 'src/hooks/useSpacesApi';
import MoveToSpaceDialog from 'src/components/MoveToSpaceDialog';
import { usePermissionState } from 'src/hooks/usePermissions';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import UserDisplay from 'src/components/UserDisplay';
import type { BackState } from 'src/navigation';
import {
  OVERVIEW_CANNOT_MOVE,
  OVERVIEW_DELETE_VIA_SPACE,
  OVERVIEW_MUST_STAY_PUBLIC,
} from 'src/spaces';
import { pageContentSx } from 'src/theme/layout';

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ReportsList() {
  const { reports, loading, error, refresh } = useReportsList();
  const { dashboardReportId, refresh: refreshDashboard } =
    useDashboardReportId();
  const {
    createReport,
    cloneReport,
    setDashboardReport,
    pinReport,
    updateReportVisibility,
    deleteReport,
    setReportSpace,
  } = useReportsMutations();
  const { spaces } = useSpacesList();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    hasPermission,
    loading: permissionsLoading,
    currentUser,
  } = usePermissionState();

  const [createOpen, setCreateOpen] = useState(
    () => searchParams.get('new') === '1',
  );
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [cloneTarget, setCloneTarget] = useState<ReportListItem | null>(null);
  const [cloneName, setCloneName] = useState('');
  const [cloning, setCloning] = useState(false);
  const [cloneError, setCloneError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<ReportListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<{
    title: string;
    message: string;
  } | null>(null);

  const [moveTarget, setMoveTarget] = useState<ReportListItem | null>(null);

  const [selectedKeys, setSelectedKeys] = useState<Array<string | number>>([]);
  const [bulkMoveOpen, setBulkMoveOpen] = useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState<string | null>(null);

  const spacesById = useMemo(
    () => new Map(spaces.map((space) => [space.space_id, space])),
    [spaces],
  );

  const selectedReports = useMemo(() => {
    const keys = new Set(selectedKeys);
    return reports.filter((report) => keys.has(report.report_id));
  }, [reports, selectedKeys]);

  const canWriteReports = hasPermission('reports:write');
  const canDeleteReports = hasPermission('reports:delete');

  const messageFromError = (err: unknown, fallback: string): string => {
    return err instanceof globalThis.Error && err.message
      ? err.message
      : fallback;
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const item = await createReport(newName.trim());
      setCreateOpen(false);
      setNewName('');
      navigate(`/app/reports/${item.report_id}?edit=true`);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCreateError((err as any)?.message ?? 'Failed to create report');
    } finally {
      setCreating(false);
    }
  };

  const handleSetDashboard = async (reportId: string) => {
    try {
      await setDashboardReport(reportId);
      refreshDashboard();
    } catch {
      // ignore – user can retry
    }
  };

  const handlePin = async (reportId: string, pinned: boolean) => {
    try {
      await pinReport(reportId, pinned);
      refresh();
    } catch {
      // ignore – user can retry
    }
  };

  const handleToggleAccess = async (report: ReportListItem) => {
    try {
      await updateReportVisibility(
        report.report_id,
        report.access.scope === 'public' ? 'private' : 'public',
      );
      refresh();
      refreshDashboard();
    } catch (err) {
      setActionError({
        title:
          report.access.scope === 'public'
            ? 'Could not unpublish report'
            : 'Could not publish report',
        message: messageFromError(err, 'Failed to update report visibility'),
      });
    }
  };

  const handleCloneOpen = (report: ReportListItem) => {
    setCloneTarget(report);
    setCloneName(`Copy of ${report.name}`);
    setCloneError(null);
  };

  const handleCloneConfirm = async () => {
    if (!cloneTarget || !cloneName.trim()) return;
    setCloning(true);
    setCloneError(null);
    try {
      const item = await cloneReport(cloneTarget.report_id, cloneName.trim());
      setCloneTarget(null);
      refresh();
      navigate(`/app/reports/${item.report_id}?edit=true`);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCloneError((err as any)?.message ?? 'Failed to clone report');
    } finally {
      setCloning(false);
    }
  };

  /**
   * Apply a bulk action to each selected report, collecting failures instead of
   * stopping at the first one.
   *
   * A partial failure is the normal case, not the exception: publishing is
   * owner-only, and overview reports refuse to move or be deleted. The backend
   * enforces those per report, so the honest UI is to attempt each one and say
   * which were skipped.
   */
  const runBulk = async (
    label: string,
    reports: ReportListItem[],
    action: (report: ReportListItem) => Promise<unknown>,
  ): Promise<boolean> => {
    setBulkBusy(true);
    const failures: string[] = [];
    try {
      for (const report of reports) {
        try {
          await action(report);
        } catch (err) {
          failures.push(`${report.name}: ${messageFromError(err, 'failed')}`);
        }
      }
    } finally {
      setBulkBusy(false);
    }
    refresh();
    refreshDashboard();
    if (failures.length > 0) {
      setActionError({
        title: `${label}: ${failures.length} of ${reports.length} failed`,
        message: failures.join('\n'),
      });
      return false;
    }
    setSelectedKeys([]);
    return true;
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteReport(deleteTarget.report_id);
      setDeleteTarget(null);
      refresh();
    } catch (err) {
      setDeleteError(messageFromError(err, 'Failed to delete report'));
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (report: ReportListItem): RowMenuAction[] => {
    const isDashboard = report.report_id === dashboardReportId;
    const isOwner = currentUser?.user_id === report.created_by;
    const canWrite = hasPermission('reports:write');
    const canDelete = hasPermission('reports:delete');
    const canSetDashboard = hasPermission('reports:set_dashboard');
    const canUpdateAccess = canWrite && isOwner;
    const isPublic = report.access.scope === 'public';
    return [
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => navigate(`/app/reports/${report.report_id}?edit=true`),
        disabled: !canWrite,
        tooltip: canWrite
          ? undefined
          : 'You do not have permission to edit reports',
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(`/app/reports/${report.report_id}/history`, {
            state: { fromLabel: 'Reports' } satisfies BackState,
          }),
      },
      {
        key: 'clone',
        label: 'Clone',
        icon: <ContentCopyIcon fontSize="small" />,
        onClick: () => handleCloneOpen(report),
        disabled: !canWrite,
        tooltip: canWrite
          ? undefined
          : 'You do not have permission to clone reports',
      },
      {
        key: 'dashboard',
        label: isDashboard ? 'Current dashboard' : 'Set as dashboard',
        icon: isDashboard ? (
          <DashboardIcon fontSize="small" color="primary" />
        ) : (
          <DashboardOutlinedIcon fontSize="small" />
        ),
        onClick: () => handleSetDashboard(report.report_id),
        disabled: isDashboard || !canSetDashboard || !isPublic,
        tooltip: canSetDashboard
          ? undefined
          : 'You do not have permission to set the dashboard',
      },
      {
        key: 'access',
        label: isPublic ? 'Unpublish' : 'Publish',
        icon: isPublic ? (
          <LockIcon fontSize="small" />
        ) : (
          <PublicIcon fontSize="small" />
        ),
        onClick: () => handleToggleAccess(report),
        // Unpublishing a space overview report is rejected with 409: the space
        // is globally visible, so its landing page has to be too.
        disabled: !canUpdateAccess || (isPublic && report.space_overview),
        tooltip:
          isPublic && report.space_overview
            ? OVERVIEW_MUST_STAY_PUBLIC
            : canUpdateAccess
              ? undefined
              : 'Only the report owner can publish or unpublish',
      },
      {
        key: 'space',
        label: 'Move to space…',
        icon: <DriveFileMoveIcon fontSize="small" />,
        onClick: () => setMoveTarget(report),
        // The overview report is an artefact of its space and stays put.
        disabled: !canWrite || report.space_overview,
        tooltip: report.space_overview
          ? OVERVIEW_CANNOT_MOVE
          : canWrite
            ? undefined
            : 'You do not have permission to move reports',
      },
      {
        key: 'pin',
        label: report.pinned ? 'Unpin from sidebar' : 'Pin to sidebar',
        icon: report.pinned ? (
          <PushPinIcon fontSize="small" color="primary" />
        ) : (
          <PushPinOutlinedIcon fontSize="small" />
        ),
        onClick: () => handlePin(report.report_id, !report.pinned),
        disabled: !canWrite,
        tooltip: canWrite
          ? undefined
          : 'You do not have permission to pin reports',
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => {
          setDeleteTarget(report);
          setDeleteError(null);
        },
        // An overview report is deleted with its space, never on its own.
        disabled: !canDelete || report.space_overview,
        tooltip: report.space_overview
          ? OVERVIEW_DELETE_VIA_SPACE
          : canDelete
            ? undefined
            : 'You do not have permission to delete reports',
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const columns: ListTableColumn<ReportListItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: { ...listTablePrimaryCellSx, width: '36%' },
      render: (report) => {
        const isDashboard = report.report_id === dashboardReportId;
        return (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              minWidth: 0,
              flexWrap: 'nowrap',
              overflow: 'hidden',
            }}
          >
            <Link
              component={RouterLink}
              to={`/app/reports/${report.report_id}`}
              underline="hover"
              color="inherit"
              sx={{
                flex: '1 1 auto',
                minWidth: 0,
                maxWidth: '100%',
                display: 'block',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontWeight: 'medium',
              }}
            >
              {report.name}
            </Link>
            {report.pinned && (
              <Tooltip title="Pinned to sidebar">
                <Chip
                  icon={<PushPinIcon sx={{ fontSize: '0.85rem !important' }} />}
                  label="Pinned"
                  size="small"
                  color="primary"
                  variant="outlined"
                  sx={{ height: 20, fontSize: '0.7rem' }}
                />
              </Tooltip>
            )}
            {isDashboard && (
              <Tooltip title="Currently set as the dashboard">
                <Chip
                  icon={
                    <DashboardIcon sx={{ fontSize: '0.85rem !important' }} />
                  }
                  label="Dashboard"
                  size="small"
                  color="primary"
                  variant="outlined"
                  sx={{ height: 20, fontSize: '0.7rem' }}
                />
              </Tooltip>
            )}
          </Box>
        );
      },
    },
    {
      key: 'visibility',
      label: 'Visibility',
      cellSx: { width: 104 },
      render: (report) => (
        <Tooltip
          title={
            report.access.scope === 'public'
              ? 'Visible to report readers'
              : 'Private to the owner'
          }
        >
          <Chip
            icon={
              report.access.scope === 'public' ? (
                <PublicIcon sx={{ fontSize: '0.85rem !important' }} />
              ) : (
                <LockIcon sx={{ fontSize: '0.85rem !important' }} />
              )
            }
            label={report.access.scope === 'public' ? 'Public' : 'Draft'}
            size="small"
            color={report.access.scope === 'public' ? 'success' : 'default'}
            variant="outlined"
          />
        </Tooltip>
      ),
    },
    {
      key: 'space',
      label: 'Space',
      hideBelow: 'md',
      cellSx: { ...listTableSecondaryCellSx, width: 180 },
      render: (report) => {
        if (!report.space_id) return '—';
        const space = spacesById.get(report.space_id);
        if (!space) return '—';
        return (
          <Link
            component={RouterLink}
            to={`/app/spaces/${space.space_id}`}
            underline="hover"
            color="inherit"
          >
            {space.name}
          </Link>
        );
      },
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: { ...listTableSecondaryCellSx, width: 96 },
      render: (report) => `v${report.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'md',
      cellSx: { ...listTableSecondaryCellSx, width: 200 },
      render: (report) => new Date(report.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (report) => (
        <UserDisplay userId={report.updated_by || report.created_by} />
      ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (report) => (
        <RowMenu actions={rowActions(report)} menuMinWidth={200} />
      ),
    },
  ];
  const filterGroups: ListTableFilterGroup<ReportListItem>[] = useMemo(
    () => [
      {
        key: 'visibility',
        label: 'Visibility',
        icon: <PublicIcon fontSize="small" />,
        options: [
          {
            key: 'draft',
            label: 'Draft',
            icon: <DraftsIcon fontSize="small" />,
            matches: (report) => report.access.scope === 'private',
          },
          {
            key: 'public',
            label: 'Public',
            icon: <PublicIcon fontSize="small" />,
            matches: (report) => report.access.scope === 'public',
          },
        ],
      },
      {
        key: 'state',
        label: 'State',
        icon: <BadgeIcon fontSize="small" />,
        options: [
          {
            key: 'pinned',
            label: 'Pinned',
            icon: <PushPinIcon fontSize="small" />,
            matches: (report) => report.pinned,
          },
          {
            key: 'dashboard',
            label: 'Dashboard',
            icon: <DashboardIcon fontSize="small" />,
            matches: (report) => report.report_id === dashboardReportId,
          },
          {
            key: 'unpinned',
            label: 'Not pinned',
            icon: <PushPinOutlinedIcon fontSize="small" />,
            matches: (report) => !report.pinned,
          },
        ],
      },
      ...(spaces.length > 0
        ? [
            {
              key: 'space',
              label: 'Space',
              icon: <WorkspacesIcon fontSize="small" />,
              options: [
                {
                  key: '__none__',
                  label: 'No space',
                  matches: (report: ReportListItem) => !report.space_id,
                },
                ...spaces.map((space) => ({
                  key: space.space_id,
                  label: space.name,
                  matches: (report: ReportListItem) =>
                    report.space_id === space.space_id,
                })),
              ],
            },
          ]
        : []),
    ],
    [dashboardReportId, spaces],
  );

  return (
    <>
      <Helmet>
        <title>Reports | Seizu</title>
      </Helmet>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Reports"
          action={
            !permissionsLoading &&
            hasPermission('reports:write') && (
              <Button
                variant="contained"
                startIcon={<Add />}
                onClick={() => setCreateOpen(true)}
              >
                New report
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load reports"
        >
          <ListTable
            rows={reports}
            columns={columns}
            getRowKey={(report) => report.report_id}
            emptyMessage="No reports yet. Create one above."
            filterGroups={filterGroups}
            selectable={canWriteReports}
            selectedKeys={selectedKeys}
            onSelectionChange={setSelectedKeys}
            bulkActions={(rows) => (
              <>
                <Button
                  size="small"
                  startIcon={<DriveFileMoveIcon fontSize="small" />}
                  disabled={bulkBusy}
                  onClick={() => setBulkMoveOpen(true)}
                >
                  Move to space
                </Button>
                <Button
                  size="small"
                  startIcon={<PushPinIcon fontSize="small" />}
                  disabled={bulkBusy}
                  onClick={() =>
                    void runBulk('Pin', rows, (report) =>
                      pinReport(report.report_id, true),
                    )
                  }
                >
                  Pin
                </Button>
                <Button
                  size="small"
                  startIcon={<PushPinOutlinedIcon fontSize="small" />}
                  disabled={bulkBusy}
                  onClick={() =>
                    void runBulk('Unpin', rows, (report) =>
                      pinReport(report.report_id, false),
                    )
                  }
                >
                  Unpin
                </Button>
                <Button
                  size="small"
                  startIcon={<PublicIcon fontSize="small" />}
                  disabled={bulkBusy}
                  onClick={() =>
                    void runBulk('Publish', rows, (report) =>
                      updateReportVisibility(report.report_id, 'public'),
                    )
                  }
                >
                  Publish
                </Button>
                {canDeleteReports && (
                  <Button
                    size="small"
                    color="error"
                    startIcon={<DeleteIcon fontSize="small" />}
                    disabled={bulkBusy}
                    onClick={() => {
                      setBulkDeleteError(null);
                      setBulkDeleteOpen(true);
                    }}
                  >
                    Delete
                  </Button>
                )}
              </>
            )}
          />
        </ListViewState>
      </Box>

      {/* Create dialog */}
      <Dialog
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          setSearchParams({}, { replace: true });
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>New report</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Report name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            error={!!createError}
            helperText={createError ?? ''}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setCreateOpen(false);
              setSearchParams({}, { replace: true });
            }}
            disabled={creating}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={creating || !newName.trim()}
          >
            {creating ? <ConstellationSpinner size={20} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Clone dialog */}
      <Dialog
        open={!!cloneTarget}
        onClose={() => setCloneTarget(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Clone report</DialogTitle>
        <DialogContent>
          {cloneError && (
            <Box sx={{ mb: 2 }}>
              <Typography color="error">{cloneError}</Typography>
            </Box>
          )}
          <TextField
            autoFocus
            fullWidth
            label="New report name"
            value={cloneName}
            onChange={(e) => setCloneName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCloneConfirm()}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCloneTarget(null)} disabled={cloning}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleCloneConfirm}
            disabled={cloning || !cloneName.trim()}
          >
            {cloning ? <ConstellationSpinner size={20} /> : 'Clone'}
          </Button>
        </DialogActions>
      </Dialog>

      {moveTarget && (
        <MoveToSpaceDialog
          open
          reportName={moveTarget.name}
          currentSpaceId={moveTarget.space_id}
          currentSubspaceId={moveTarget.subspace_id}
          onClose={() => setMoveTarget(null)}
          onConfirm={async (spaceId, subspaceId) => {
            await setReportSpace(moveTarget.report_id, spaceId, subspaceId);
            refresh();
          }}
        />
      )}

      {bulkMoveOpen && (
        <MoveToSpaceDialog
          open
          reportName={`${selectedReports.length} reports`}
          currentSpaceId={null}
          currentSubspaceId={null}
          onClose={() => setBulkMoveOpen(false)}
          onConfirm={async (spaceId, subspaceId) => {
            // Overview reports cannot leave their space (409), so they are
            // filtered out rather than reported as failures.
            const movable = selectedReports.filter(
              (report) => !report.space_overview,
            );
            await runBulk('Move to space', movable, (report) =>
              setReportSpace(report.report_id, spaceId, subspaceId),
            );
          }}
        />
      )}

      <ConfirmDeleteDialog
        open={bulkDeleteOpen}
        title={`Delete ${selectedReports.length} report(s)?`}
        deleting={bulkBusy}
        error={bulkDeleteError}
        onClose={() => setBulkDeleteOpen(false)}
        onConfirm={async () => {
          const deletable = selectedReports.filter(
            (report) => !report.space_overview,
          );
          const ok = await runBulk('Delete', deletable, (report) =>
            deleteReport(report.report_id),
          );
          if (ok) setBulkDeleteOpen(false);
        }}
      >
        Permanently delete{' '}
        <strong>
          {selectedReports.filter((r) => !r.space_overview).length}
        </strong>{' '}
        report(s) and all their versions? This cannot be undone.
        {selectedReports.some((r) => r.space_overview) && (
          <>
            {' '}
            Space overview reports in the selection are skipped — delete their
            space instead.
          </>
        )}
      </ConfirmDeleteDialog>

      {/* Delete confirmation dialog */}
      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete report?"
        deleting={deleting}
        error={deleteError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        versions? This cannot be undone.
      </ConfirmDeleteDialog>

      <Dialog
        open={!!actionError}
        onClose={() => setActionError(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>{actionError?.title}</DialogTitle>
        <DialogContent>
          {/* Bulk failures arrive as one newline-separated line per report. */}
          <DialogContentText sx={{ whiteSpace: 'pre-line' }}>
            {actionError?.message}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button variant="contained" onClick={() => setActionError(null)}>
            OK
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

export default ReportsList;
