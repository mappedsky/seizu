import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  ListItemIcon,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListSubheader,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import CreateNewFolderOutlinedIcon from '@mui/icons-material/CreateNewFolderOutlined';
import PostAddOutlinedIcon from '@mui/icons-material/PostAddOutlined';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import ChevronLeft from '@mui/icons-material/ChevronLeft';
import ChevronRight from '@mui/icons-material/ChevronRight';
import DeleteIcon from '@mui/icons-material/Delete';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import Insights from '@mui/icons-material/Insights';
import EditIcon from '@mui/icons-material/Edit';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircleOutlined';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import { listTableTruncateSx } from 'src/components/ListTable';
import type { ReportListItem } from 'src/hooks/useReportsApi';
import type { SpaceTree, SubspaceItem } from 'src/hooks/useSpacesApi';

const PANEL_WIDTH = 260;
// Tightened from the MUI default so the footer actions sit at the same rhythm
// as the report rows above them.
const footerActionIconSx = { minWidth: 32 } as const;

interface SpaceReportsPanelProps {
  open: boolean;
  onToggle: () => void;
  tree: SpaceTree;
  activeReportId: string | undefined;
  /** spaces:write — creating and renaming sub-spaces. */
  canWriteSpaces: boolean;
  /** spaces:delete — deleting sub-spaces. */
  canDeleteSpaces: boolean;
  /** reports:write — filing reports into or out of the space. */
  canWriteReports: boolean;
  onSelectReport: (reportId: string) => void;
  // Promise<unknown>: these can be wired straight to the mutation hooks, whose
  // return values the panel has no use for.
  onCreateSubspace: (name: string) => Promise<unknown>;
  onRenameSubspace: (subspaceId: string, name: string) => Promise<unknown>;
  onDeleteSubspace: (subspaceId: string) => Promise<unknown>;
  onSetReportSubspace: (
    reportId: string,
    subspaceId: string | null,
  ) => Promise<void>;
  onRemoveReportFromSpace: (reportId: string) => Promise<void>;
  onSetOverview: (reportId: string | null) => Promise<unknown>;
  onCreateReport: () => void;
}

interface ReportGroup {
  subspace: SubspaceItem | null;
  reports: ReportListItem[];
}

function SpaceReportsPanel({
  open,
  onToggle,
  tree,
  activeReportId,
  canWriteSpaces,
  canDeleteSpaces,
  canWriteReports,
  onSelectReport,
  onCreateSubspace,
  onRenameSubspace,
  onDeleteSubspace,
  onSetReportSubspace,
  onRemoveReportFromSpace,
  onSetOverview,
  onCreateReport,
}: SpaceReportsPanelProps) {
  const navigate = useNavigate();
  const [subspaceDialogOpen, setSubspaceDialogOpen] = useState(false);
  const [subspaceEditTarget, setSubspaceEditTarget] =
    useState<SubspaceItem | null>(null);
  const [subspaceName, setSubspaceName] = useState('');
  const [savingSubspace, setSavingSubspace] = useState(false);
  const [subspaceError, setSubspaceError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SubspaceItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [moveTarget, setMoveTarget] = useState<ReportListItem | null>(null);

  const groups = useMemo<ReportGroup[]>(() => {
    // Every member report is listed, the pinned one included — it is an
    // ordinary report that happens to be the space's landing page.
    const members = tree.reports;
    // The API already blanks out any subspace_id left behind by a deleted
    // sub-space, so anything unmatched here is genuinely ungrouped.
    const ungrouped = members.filter((report) => !report.subspace_id);
    return [
      { subspace: null, reports: ungrouped },
      // Empty sub-spaces still render, so they stay renameable and deletable.
      ...tree.subspaces.map((subspace) => ({
        subspace,
        reports: members.filter(
          (report) => report.subspace_id === subspace.subspace_id,
        ),
      })),
    ];
  }, [tree]);

  const openCreateSubspace = () => {
    setSubspaceEditTarget(null);
    setSubspaceName('');
    setSubspaceError(null);
    setSubspaceDialogOpen(true);
  };

  const openRenameSubspace = (subspace: SubspaceItem) => {
    setSubspaceEditTarget(subspace);
    setSubspaceName(subspace.name);
    setSubspaceError(null);
    setSubspaceDialogOpen(true);
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
        await onRenameSubspace(subspaceEditTarget.subspace_id, trimmed);
      } else {
        await onCreateSubspace(trimmed);
      }
      setSubspaceDialogOpen(false);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setSubspaceError((err as any)?.message ?? 'Failed to save sub-space.');
    } finally {
      setSavingSubspace(false);
    }
  };

  const handleDeleteSubspace = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDeleteSubspace(deleteTarget.subspace_id);
      setDeleteTarget(null);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setDeleteError((err as any)?.message ?? 'Failed to delete sub-space.');
    } finally {
      setDeleting(false);
    }
  };

  const subspaceActions = (subspace: SubspaceItem): RowMenuAction[] => [
    {
      key: 'rename',
      label: 'Rename',
      icon: <EditIcon fontSize="small" />,
      onClick: () => openRenameSubspace(subspace),
      disabled: !canWriteSpaces,
    },
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

  const reportActions = (report: ReportListItem): RowMenuAction[] => {
    const isOverview = report.report_id === tree.space.overview_report_id;
    return [
      {
        key: 'overview',
        label: isOverview ? 'Clear space overview' : 'Set as space overview',
        icon: isOverview ? (
          <StarIcon fontSize="small" color="primary" />
        ) : (
          <StarBorderIcon fontSize="small" />
        ),
        onClick: () => void onSetOverview(isOverview ? null : report.report_id),
        disabled: !canWriteSpaces,
        tooltip: canWriteSpaces
          ? undefined
          : 'You do not have permission to change the space overview',
      },
      {
        key: 'move',
        label: 'Move to sub-space…',
        icon: <DriveFileMoveIcon fontSize="small" />,
        onClick: () => setMoveTarget(report),
        disabled: !canWriteReports || tree.subspaces.length === 0,
        tooltip:
          tree.subspaces.length === 0
            ? 'This space has no sub-spaces'
            : undefined,
      },
      {
        key: 'remove',
        label: 'Remove from space',
        icon: <RemoveCircleOutlineIcon fontSize="small" />,
        onClick: () => void onRemoveReportFromSpace(report.report_id),
        disabled: !canWriteReports,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  if (!open) {
    return (
      <Box sx={{ p: 1, borderRight: 1, borderColor: 'divider' }}>
        <Tooltip title="Show reports">
          <IconButton aria-label="Show reports" size="small" onClick={onToggle}>
            <ChevronRight />
          </IconButton>
        </Tooltip>
      </Box>
    );
  }

  return (
    <>
      <Box
        sx={{
          width: PANEL_WIDTH,
          flexShrink: 0,
          borderRight: 1,
          borderColor: 'divider',
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          // The scroll region is the list below, not the whole panel, so the
          // header stays put instead of scrolling out from under the navbar.
          overflow: 'hidden',
        }}
      >
        {/* Title row: the space's own name, so the list below is only reports.
            Creation lives in the footer action group. */}
        <Box
          sx={{
            alignItems: 'center',
            display: 'flex',
            flexShrink: 0,
            gap: 0.5,
            minHeight: 32,
            px: 2,
            pt: 2,
            pb: 1,
          }}
        >
          <Tooltip
            title={tree.space.name}
            placement="top"
            arrow
            disableInteractive
          >
            <Typography
              variant="subtitle2"
              sx={{
                ...listTableTruncateSx,
                flexGrow: 1,
                fontWeight: 600,
              }}
            >
              {tree.space.name}
            </Typography>
          </Tooltip>
          <Tooltip title="Hide reports">
            <IconButton
              aria-label="Hide reports"
              size="small"
              onClick={onToggle}
            >
              <ChevronLeft />
            </IconButton>
          </Tooltip>
        </Box>
        <Divider />

        {/* Only the list scrolls. The header and footer must stay outside this
            box, or the header scrolls up under the fixed navbar. */}
        <Box
          data-testid="space-reports-scroll"
          sx={{ flex: 1, minHeight: 0, overflowY: 'auto', pt: 0.5 }}
        >
          {groups.map((group) => {
            const key = group.subspace?.subspace_id ?? '__ungrouped__';
            if (!group.subspace && group.reports.length === 0) return null;
            return (
              <List
                key={key}
                dense
                disablePadding
                subheader={
                  group.subspace ? (
                    <ListSubheader
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.5,
                        lineHeight: '36px',
                      }}
                    >
                      <Tooltip
                        title={group.subspace.name}
                        placement="top"
                        arrow
                        disableInteractive
                      >
                        <Box
                          component="span"
                          sx={{
                            flexGrow: 1,
                            minWidth: 0,
                            ...listTableTruncateSx,
                          }}
                        >
                          {group.subspace.name}
                        </Box>
                      </Tooltip>
                      <RowMenu
                        actions={subspaceActions(group.subspace)}
                        label="Sub-space actions"
                      />
                    </ListSubheader>
                  ) : undefined
                }
              >
                {group.reports.length === 0 && group.subspace && (
                  <ListItem>
                    <Typography variant="caption" color="text.secondary">
                      No reports
                    </Typography>
                  </ListItem>
                )}
                {group.reports.map((report) => (
                  <ListItem
                    key={report.report_id}
                    disablePadding
                    secondaryAction={
                      canWriteReports ? (
                        <RowMenu
                          actions={reportActions(report)}
                          label="Report actions"
                          menuMinWidth={200}
                        />
                      ) : undefined
                    }
                  >
                    <ListItemButton
                      selected={activeReportId === report.report_id}
                      onClick={() => onSelectReport(report.report_id)}
                    >
                      <Tooltip
                        title={report.name}
                        placement="top"
                        arrow
                        disableInteractive
                      >
                        <Typography
                          variant="body2"
                          sx={{ ...listTableTruncateSx, width: '100%' }}
                        >
                          {report.name}
                        </Typography>
                      </Tooltip>
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            );
          })}
        </Box>

        {/* Footer actions: one styled group so creating and navigating away
            read as the same kind of affordance. The empty-state guidance lives
            in the main region, which has room for it. */}
        <Box sx={{ borderTop: 1, borderColor: 'divider', flexShrink: 0 }}>
          <List dense disablePadding>
            {canWriteSpaces && (
              <ListItem disablePadding>
                <ListItemButton onClick={openCreateSubspace}>
                  <ListItemIcon sx={footerActionIconSx}>
                    <CreateNewFolderOutlinedIcon fontSize="small" />
                  </ListItemIcon>
                  <Typography variant="body2">New sub-space</Typography>
                </ListItemButton>
              </ListItem>
            )}
            {canWriteReports && (
              <ListItem disablePadding>
                <ListItemButton onClick={onCreateReport}>
                  <ListItemIcon sx={footerActionIconSx}>
                    <PostAddOutlinedIcon fontSize="small" />
                  </ListItemIcon>
                  <Typography variant="body2">New report</Typography>
                </ListItemButton>
              </ListItem>
            )}
            <ListItem disablePadding>
              <ListItemButton onClick={() => navigate('/app/reports')}>
                <ListItemIcon sx={footerActionIconSx}>
                  <Insights fontSize="small" />
                </ListItemIcon>
                <Typography variant="body2">Go to all reports</Typography>
              </ListItemButton>
            </ListItem>
          </List>
        </Box>
      </Box>

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
            <ListItem disablePadding>
              <ListItemButton
                selected={!moveTarget?.subspace_id}
                onClick={async () => {
                  if (!moveTarget) return;
                  await onSetReportSubspace(moveTarget.report_id, null);
                  setMoveTarget(null);
                }}
              >
                <Typography variant="body2">Ungrouped</Typography>
              </ListItemButton>
            </ListItem>
            {tree.subspaces.map((subspace) => (
              <ListItem key={subspace.subspace_id} disablePadding>
                <ListItemButton
                  selected={moveTarget?.subspace_id === subspace.subspace_id}
                  onClick={async () => {
                    if (!moveTarget) return;
                    await onSetReportSubspace(
                      moveTarget.report_id,
                      subspace.subspace_id,
                    );
                    setMoveTarget(null);
                  }}
                >
                  <Typography variant="body2">{subspace.name}</Typography>
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMoveTarget(null)}>Cancel</Button>
        </DialogActions>
      </Dialog>

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete sub-space?"
        deleting={deleting}
        error={deleteError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteSubspace}
      >
        Delete <strong>{deleteTarget?.name}</strong>? Its reports stay in this
        space and move to the ungrouped list.
      </ConfirmDeleteDialog>
    </>
  );
}

export default SpaceReportsPanel;
