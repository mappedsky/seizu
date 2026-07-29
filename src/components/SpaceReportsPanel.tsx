import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
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
import AddIcon from '@mui/icons-material/Add';
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

interface SpaceReportsPanelProps {
  open: boolean;
  onToggle: () => void;
  tree: SpaceTree;
  activeReportId: string | undefined;
  canWrite: boolean;
  canDelete: boolean;
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
  canWrite,
  canDelete,
  onSelectReport,
  onCreateSubspace,
  onRenameSubspace,
  onDeleteSubspace,
  onSetReportSubspace,
  onRemoveReportFromSpace,
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
    // The overview report is the main view, not a sidebar entry.
    const members = tree.reports.filter(
      (report) => report.report_id !== tree.space.overview_report_id,
    );
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

  // "Empty" means nothing beyond the overview report; sub-spaces alone don't
  // count, since an empty sub-space still needs somewhere to file reports from.
  const hasMemberReports = groups.some((group) => group.reports.length > 0);

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
      disabled: !canWrite,
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteTarget(subspace);
      },
      disabled: !canDelete,
      destructive: true,
      dividerBefore: true,
    },
  ];

  const reportActions = (report: ReportListItem): RowMenuAction[] => [
    {
      key: 'move',
      label: 'Move to sub-space…',
      icon: <DriveFileMoveIcon fontSize="small" />,
      onClick: () => setMoveTarget(report),
      disabled: !canWrite || tree.subspaces.length === 0,
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
      disabled: !canWrite,
      destructive: true,
      dividerBefore: true,
    },
  ];

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
        {/* Title row, following the chat sidebar: label, then the create
            affordance as a tooltipped icon, then the collapse toggle. */}
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
          <Typography variant="subtitle2" sx={{ flexGrow: 1, minWidth: 0 }}>
            Space
          </Typography>
          {canWrite && (
            <Tooltip title="New sub-space">
              <IconButton
                aria-label="New sub-space"
                size="small"
                onClick={openCreateSubspace}
              >
                <AddIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
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
          <List dense disablePadding>
            <ListItem disablePadding>
              {/* The space's own entry, named after the space rather than
                  labelled "Overview" — it is the space's landing page, so the
                  space name is what identifies it. */}
              <ListItemButton
                selected={activeReportId === tree.space.overview_report_id}
                onClick={() => onSelectReport(tree.space.overview_report_id)}
              >
                <Tooltip
                  title={tree.space.name}
                  placement="top"
                  arrow
                  disableInteractive
                >
                  <Typography
                    variant="body2"
                    sx={{
                      ...listTableTruncateSx,
                      fontWeight: 600,
                      width: '100%',
                    }}
                  >
                    {tree.space.name}
                  </Typography>
                </Tooltip>
              </ListItemButton>
            </ListItem>
          </List>
          <Divider />

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
                      canWrite ? (
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

        <Box sx={{ borderTop: 1, borderColor: 'divider', flexShrink: 0, p: 1 }}>
          {hasMemberReports ? (
            <Button
              size="small"
              fullWidth
              onClick={() => navigate('/app/reports')}
            >
              All reports
            </Button>
          ) : (
            // Nothing filed here yet: point at where reports get filed rather
            // than offering a bare navigation link.
            <Box sx={{ px: 1, py: 0.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                No reports yet
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: 'block', mb: 1 }}
              >
                Add reports to this space with{' '}
                <Box component="span" sx={{ fontStyle: 'italic' }}>
                  Move to space
                </Box>{' '}
                from the reports list.
              </Typography>
              <Button
                size="small"
                variant="outlined"
                fullWidth
                startIcon={<Insights fontSize="small" />}
                onClick={() => navigate('/app/reports')}
              >
                Go to reports
              </Button>
            </Box>
          )}
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
