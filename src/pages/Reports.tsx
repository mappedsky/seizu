import { useState, useEffect } from 'react';
import {
  useParams,
  useNavigate,
  useSearchParams,
  useLocation,
} from 'react-router-dom';
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
import Error from '@mui/icons-material/Error';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import LockIcon from '@mui/icons-material/Lock';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PublicIcon from '@mui/icons-material/Public';
import RefreshIcon from '@mui/icons-material/Refresh';

import ReportView from 'src/components/ReportView';
import EditableReportView from 'src/components/EditableReportView';
import {
  useReport,
  useReportsMutations,
  updateCachedReportCapabilities,
} from 'src/hooks/useReportsApi';
import { Report } from 'src/config.context';
import { usePermissionState } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

function Reports() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    hasPermission,
    loading: permissionsLoading,
    currentUser,
  } = usePermissionState();

  const [editMode, setEditMode] = useState(searchParams.get('edit') === 'true');
  const [displayedReport, setDisplayedReport] = useState<Report | undefined>(
    undefined,
  );
  const [displayedName, setDisplayedName] = useState<string | undefined>(
    undefined,
  );
  const [displayedAccessScope, setDisplayedAccessScope] = useState<
    'private' | 'public' | undefined
  >(undefined);
  const [displayedOwnerId, setDisplayedOwnerId] = useState<string | undefined>(
    undefined,
  );
  const [displayedQueryCapabilities, setDisplayedQueryCapabilities] = useState<
    Record<string, string> | undefined
  >(undefined);

  const {
    report,
    name,
    reportVersion,
    queryCapabilities,
    loading,
    error,
    refresh: refreshCapabilities,
  } = useReport(id);
  const { saveReportVersion, cloneReport, updateReportVisibility } =
    useReportsMutations();

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
      navigate(`/app/reports/${item.report_id}?edit=true`);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCloneError((err as any)?.message ?? 'Failed to clone report');
    } finally {
      setCloning(false);
    }
  };

  useEffect(() => {
    if (report) {
      const reportName = name?.trim() || report.name;
      setDisplayedReport(reportName ? { ...report, name: reportName } : report);
    }
    if (name) setDisplayedName(name);
    if (reportVersion) {
      setDisplayedAccessScope(reportVersion.access.scope);
      setDisplayedOwnerId(reportVersion.report_created_by);
    }
    setDisplayedQueryCapabilities(queryCapabilities);
  }, [report, name, reportVersion, queryCapabilities]);

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
      name: version.name,
      reportVersion: version,
      queryCapabilities: version.query_capabilities,
    });
    setDisplayedReport(
      savedName ? { ...version.config, name: savedName } : version.config,
    );
    setDisplayedName(savedName);
    setDisplayedQueryCapabilities(version.query_capabilities);
    window.dispatchEvent(new Event('seizu:reports-updated'));
    setEditMode(false);
    // Navigate back to view mode (clears ?edit param)
    navigate(`/app/reports/${id}`, { replace: true });
  }

  async function handleToggleAccess() {
    if (!id || !displayedAccessScope) return;
    setUpdatingAccess(true);
    try {
      const updated = await updateReportVisibility(
        id,
        displayedAccessScope === 'public' ? 'private' : 'public',
      );
      setDisplayedAccessScope(updated.access.scope);
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
        queryCapabilities={displayedQueryCapabilities}
        toolbarActions={({ onRefresh, refreshedAtLabel }) => {
          const secondaryActions = [
            {
              key: 'history',
              label: 'History',
              icon: <HistoryIcon fontSize="small" />,
              disabled: false,
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
                    disabled: !canUpdateAccess || updatingAccess,
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
                {secondaryActions.map((action) => (
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
                ))}
              </Menu>
            </>
          );
        }}
        onRefreshCapabilities={refreshCapabilities}
      />

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

export default Reports;
