import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import Error from '@mui/icons-material/Error';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import LockIcon from '@mui/icons-material/Lock';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PublicIcon from '@mui/icons-material/Public';
import RefreshIcon from '@mui/icons-material/Refresh';

import ReportView from 'src/components/ReportView';
import EditableReportView from 'src/components/EditableReportView';
import MoveToSpaceDialog from 'src/components/MoveToSpaceDialog';
import {
  useReport,
  useReportsMutations,
  updateCachedReportCapabilities,
  type ReportVersion,
} from 'src/hooks/useReportsApi';
import { Report } from 'src/config.context';
import { usePermissionState } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

export interface ReportPaneProps {
  /** The report to render. */
  id: string | undefined;
  /**
   * Where this report lives, used for the `?edit` param sync target and for
   * post-save/post-clone navigation. Defaults to the top-level report route;
   * the space detail page passes its own so a report edited inside a space
   * returns to the space rather than jumping out of it.
   */
  reportPath?: (reportId: string) => string;
  /**
   * Whether the report toolbar may pin itself to the viewport.
   *
   * The sticky toolbar is `position: fixed` and spans from the app sidebar to
   * the right edge, so inside a space it would sit on top of the space's own
   * report sidebar. The space detail page turns it off and scrolls the report
   * pane instead.
   */
  stickyToolbar?: boolean;
}

function defaultReportPath(reportId: string) {
  return `/app/reports/${reportId}`;
}

function ReportPane({
  id,
  reportPath = defaultReportPath,
  stickyToolbar = true,
}: ReportPaneProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    hasPermission,
    loading: permissionsLoading,
    currentUser,
  } = usePermissionState();

  const [editMode, setEditMode] = useState(searchParams.get('edit') === 'true');
  // Visibility and space membership are changed here without producing a new
  // report version, so the loaded version cannot show them until it is fetched
  // again. Each local change carries the version it was applied to, so a
  // genuinely newer load supersedes it rather than being masked by it — which
  // is what copying every field into mirror state through an effect used to do
  // for all six of them.
  const [localAccessScope, setLocalAccessScope] = useState<{
    base: ReportVersion;
    scope: 'private' | 'public';
  } | null>(null);
  const [localSpace, setLocalSpace] = useState<{
    base: ReportVersion;
    spaceId: string | null;
    subspaceId: string | null;
  } | null>(null);

  const {
    report,
    name,
    reportVersion,
    queryCapabilities,
    loading,
    error,
    refresh: refreshCapabilities,
  } = useReport(id);
  const {
    saveReportVersion,
    cloneReport,
    updateReportVisibility,
    setReportSpace,
  } = useReportsMutations();

  // Memoised so the report handed to the view keeps its identity between
  // loads: the panels below key effects on it.
  const displayedReport = useMemo(() => {
    if (!report) return undefined;
    const reportName = name?.trim() || report.name;
    return reportName ? { ...report, name: reportName } : report;
  }, [report, name]);
  const displayedName = name;
  const displayedQueryCapabilities = queryCapabilities;
  const displayedOwnerId = reportVersion?.report_created_by;
  const displayedAccessScope =
    localAccessScope && localAccessScope.base === reportVersion
      ? localAccessScope.scope
      : reportVersion?.access.scope;
  const displayedSpace =
    localSpace && localSpace.base === reportVersion
      ? { spaceId: localSpace.spaceId, subspaceId: localSpace.subspaceId }
      : {
          spaceId: reportVersion?.space_id ?? null,
          subspaceId: reportVersion?.subspace_id ?? null,
        };

  const [moveOpen, setMoveOpen] = useState(false);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [cloneName, setCloneName] = useState('');
  const [cloning, setCloning] = useState(false);
  const [cloneError, setCloneError] = useState<string | null>(null);
  const [updatingAccess, setUpdatingAccess] = useState(false);
  const [actionsAnchor, setActionsAnchor] = useState<null | HTMLElement>(null);

  const handleCloneOpen = () => {
    setCloneName(`Copy of ${displayedName ?? ''}`);
    setCloneError(null);
    setCloneOpen(true);
  };

  const handleCloneConfirm = async () => {
    if (!id || !cloneName.trim()) return;
    setCloning(true);
    setCloneError(null);
    try {
      const item = await cloneReport(id, cloneName.trim());
      setCloneOpen(false);
      navigate(`${reportPath(item.report_id)}?edit=true`);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCloneError((err as any)?.message ?? 'Failed to clone report');
    } finally {
      setCloning(false);
    }
  };

  // Sync edit param in URL
  useEffect(() => {
    if (editMode) {
      setSearchParams({ edit: 'true' }, { replace: true });
    } else {
      setSearchParams({}, { replace: true });
    }
  }, [editMode, setSearchParams]);

  function handleEnterEdit() {
    setEditMode(true);
  }

  function handleCancel() {
    setEditMode(false);
  }

  async function handleSave(updatedReport: Report, comment: string) {
    if (!id) return;
    const version = await saveReportVersion(
      id,
      updatedReport,
      comment || undefined,
      true,
    );
    const savedName = updatedReport.name?.trim() || version.name;
    // Keep the capabilities cache consistent so navigating away and back after save
    // returns the new version's tokens rather than the pre-save ones.
    updateCachedReportCapabilities(id, {
      report: version.config,
      name: savedName || version.name,
      reportVersion: version,
      queryCapabilities: version.query_capabilities ?? undefined,
    });
    window.dispatchEvent(new Event('seizu:reports-updated'));
    setEditMode(false);
    // Navigate back to view mode (clears ?edit param)
    navigate(reportPath(id), { replace: true });
  }

  async function handleToggleAccess() {
    if (!id || !displayedAccessScope) return;
    setUpdatingAccess(true);
    try {
      const updated = await updateReportVisibility(
        id,
        displayedAccessScope === 'public' ? 'private' : 'public',
      );
      if (reportVersion)
        setLocalAccessScope({
          base: reportVersion,
          scope: updated.access.scope,
        });
    } finally {
      setUpdatingAccess(false);
    }
  }

  if ((loading && !displayedReport) || permissionsLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if ((error || (!displayedReport && !report)) && !loading) {
    return (
      <Box
        sx={{ ...pageContentSx, display: 'flex', alignItems: 'center', gap: 1 }}
      >
        <Error />
        <Typography>Failed to load report</Typography>
      </Box>
    );
  }

  if (!displayedReport) return null;

  if (displayedQueryCapabilities === undefined) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  const isOwner = currentUser?.user_id === displayedOwnerId;
  const canUpdateAccess = hasPermission('reports:write') && isOwner;
  const canWriteReports = hasPermission('reports:write');
  const actionsMenuOpen = Boolean(actionsAnchor);

  const closeActionsMenu = () => {
    setActionsAnchor(null);
  };

  if (editMode) {
    return (
      <EditableReportView
        report={displayedReport}
        reportId={id ?? ''}
        onSave={handleSave}
        onCancel={handleCancel}
      />
    );
  }

  return (
    <Box>
      <ReportView
        report={displayedReport}
        title={displayedName}
        showTitle
        stickyToolbar={stickyToolbar}
        queryCapabilities={displayedQueryCapabilities}
        toolbarActions={({ onRefresh, refreshedAtLabel }) => {
          const secondaryActions = [
            {
              key: 'history',
              label: 'History',
              icon: <HistoryIcon fontSize="small" />,
              disabled: false,
              tooltip: undefined as string | undefined,
              onClick: () =>
                navigate(`/app/reports/${id}/history`, {
                  state: {
                    fromLabel: displayedName ?? 'report',
                    originReturnTo: `${location.pathname}${location.search}`,
                  } satisfies BackState,
                }),
            },
            ...(canWriteReports
              ? [
                  {
                    key: 'visibility',
                    label:
                      displayedAccessScope === 'public'
                        ? 'Unpublish'
                        : 'Publish',
                    icon: updatingAccess ? (
                      <ConstellationSpinner size={18} />
                    ) : displayedAccessScope === 'public' ? (
                      <LockIcon fontSize="small" />
                    ) : (
                      <PublicIcon fontSize="small" />
                    ),
                    // Reports in a space are public, so unpublishing means
                    // removing it from its space first.
                    disabled:
                      !canUpdateAccess ||
                      updatingAccess ||
                      (displayedAccessScope === 'public' &&
                        !!displayedSpace.spaceId),
                    tooltip:
                      displayedAccessScope === 'public' &&
                      displayedSpace.spaceId
                        ? 'Remove the report from its space before unpublishing it'
                        : undefined,
                    onClick: handleToggleAccess,
                  },
                  {
                    key: 'clone',
                    label: 'Clone',
                    icon: <ContentCopyIcon fontSize="small" />,
                    disabled: false,
                    onClick: handleCloneOpen,
                  },
                ]
              : []),
            ...(canWriteReports
              ? [
                  {
                    key: 'space',
                    label: 'Move to space…',
                    icon: <DriveFileMoveIcon fontSize="small" />,
                    // A draft cannot be filed into a space at all.
                    disabled: displayedAccessScope !== 'public',
                    tooltip:
                      displayedAccessScope === 'public'
                        ? undefined
                        : 'Publish the report before filing it into a space',
                    onClick: () => setMoveOpen(true),
                  },
                ]
              : []),
          ];

          return (
            <>
              {displayedAccessScope && (
                <Chip
                  icon={
                    displayedAccessScope === 'public' ? (
                      <PublicIcon />
                    ) : (
                      <LockIcon />
                    )
                  }
                  label={displayedAccessScope === 'public' ? 'Public' : 'Draft'}
                  size="small"
                  color={
                    displayedAccessScope === 'public' ? 'success' : 'default'
                  }
                  variant="outlined"
                  sx={{ alignSelf: 'center' }}
                />
              )}
              {canWriteReports && (
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<EditIcon />}
                  onClick={handleEnterEdit}
                >
                  Edit Report
                </Button>
              )}
              <Tooltip title="More actions">
                <IconButton
                  aria-label="More actions"
                  size="small"
                  onClick={(event) => setActionsAnchor(event.currentTarget)}
                >
                  <MoreVertIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Menu
                anchorEl={actionsAnchor}
                open={actionsMenuOpen}
                onClose={closeActionsMenu}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
                transformOrigin={{ vertical: 'top', horizontal: 'right' }}
                slotProps={{ paper: { sx: { minWidth: 180 } } }}
              >
                {refreshedAtLabel && (
                  <MenuItem disabled sx={{ opacity: '1 !important' }}>
                    <Typography variant="caption" color="text.secondary">
                      {refreshedAtLabel}
                    </Typography>
                  </MenuItem>
                )}
                <MenuItem
                  onClick={() => {
                    closeActionsMenu();
                    onRefresh();
                  }}
                >
                  <ListItemIcon>
                    <RefreshIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText>Refresh data</ListItemText>
                </MenuItem>
                <Divider />
                {secondaryActions.map((action) => {
                  const item = (
                    <MenuItem
                      key={action.key}
                      onClick={() => {
                        closeActionsMenu();
                        action.onClick();
                      }}
                      disabled={action.disabled}
                    >
                      <ListItemIcon>{action.icon}</ListItemIcon>
                      <ListItemText>{action.label}</ListItemText>
                    </MenuItem>
                  );
                  if (!action.tooltip) return item;
                  // A disabled MenuItem receives no pointer events, so the
                  // tooltip has to hang off a wrapper — same approach as RowMenu.
                  return (
                    <Tooltip key={action.key} title={action.tooltip}>
                      <span>{item}</span>
                    </Tooltip>
                  );
                })}
              </Menu>
            </>
          );
        }}
        onRefreshCapabilities={refreshCapabilities}
      />

      {moveOpen && id && (
        <MoveToSpaceDialog
          open
          reportName={displayedName ?? ''}
          currentSpaceId={displayedSpace.spaceId}
          currentSubspaceId={displayedSpace.subspaceId}
          onClose={() => setMoveOpen(false)}
          onConfirm={async (spaceId, subspaceId) => {
            const updated = await setReportSpace(id, spaceId, subspaceId);
            if (reportVersion)
              setLocalSpace({
                base: reportVersion,
                spaceId: updated.space_id ?? null,
                subspaceId: updated.subspace_id ?? null,
              });
          }}
        />
      )}

      <Dialog
        open={cloneOpen}
        onClose={() => setCloneOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Clone report</DialogTitle>
        <DialogContent>
          {cloneError && (
            <Typography color="error" sx={{ mb: 1 }}>
              {cloneError}
            </Typography>
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
          <Button onClick={() => setCloneOpen(false)} disabled={cloning}>
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
    </Box>
  );
}

export default ReportPane;
