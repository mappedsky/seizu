import { useCallback, useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from '@mui/material';
import Error from '@mui/icons-material/Error';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ReportPane from 'src/components/ReportPane';
import SpaceReportsPanel from 'src/components/SpaceReportsPanel';
import { useReportsMutations } from 'src/hooks/useReportsApi';
import {
  useSpaceMutations,
  useSpaceTree,
  useSubspaceMutations,
} from 'src/hooks/useSpacesApi';
import { usePermissions } from 'src/hooks/usePermissions';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';
import { pageContentSx } from 'src/theme/layout';

function SpaceDetail() {
  const { spaceId, reportId } = useParams();
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const [panelOpen, setPanelOpen] = useState(true);

  // No explicit refresh wiring here: every mutation below broadcasts an
  // invalidation (sub-spaces and the overview pointer via the spaces signal,
  // report membership via the reports signal) that useSpaceTree subscribes to,
  // so adding refresh() calls on top would fetch the tree twice per change.
  const { tree, loading, error } = useSpaceTree(spaceId ?? null);
  const { createReport, setReportSpace } = useReportsMutations();
  const { setSpaceOverview } = useSpaceMutations();
  const { createSubspace, updateSubspace, deleteSubspace } =
    useSubspaceMutations(spaceId ?? '');

  // Sub-space mutations and the overview pointer need spaces:write; filing a
  // report needs reports:write. Gating both on one permission shows custom
  // roles actions they cannot run, and hides actions they can.
  const canWriteSpaces = hasPermission('spaces:write');
  const canDeleteSpaces = hasPermission('spaces:delete');
  const canWriteReports = hasPermission('reports:write');

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const handleSetReportSubspace = useCallback(
    async (targetReportId: string, subspaceId: string | null) => {
      if (!spaceId) return;
      await setReportSpace(targetReportId, spaceId, subspaceId);
    },
    [setReportSpace, spaceId],
  );

  const handleRemoveReportFromSpace = useCallback(
    async (targetReportId: string) => {
      await setReportSpace(targetReportId, null, null);
      // If the report being removed is the one on screen, fall back to the
      // space root rather than leaving a report the space no longer contains.
      if (targetReportId === reportId && spaceId) {
        navigate(`/app/spaces/${spaceId}`, { replace: true });
      }
    },
    [setReportSpace, reportId, spaceId, navigate],
  );

  const handleSetOverview = useCallback(
    async (targetReportId: string | null) => {
      if (!spaceId) return;
      await setSpaceOverview(spaceId, targetReportId);
    },
    [setSpaceOverview, spaceId],
  );

  const reportPath = useCallback(
    (targetReportId: string) =>
      `/app/spaces/${spaceId}/reports/${targetReportId}`,
    [spaceId],
  );

  const handleCreateReport = async () => {
    if (!newName.trim() || !spaceId) return;
    setCreating(true);
    setCreateError(null);
    try {
      // Created and filed in one call, so setting a space up does not mean
      // leaving it to make a report and coming back.
      const item = await createReport(newName.trim(), spaceId);
      setCreateOpen(false);
      setNewName('');
      navigate(`${reportPath(item.report_id)}?edit=true`);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCreateError((err as any)?.message ?? 'Failed to create report');
    } finally {
      setCreating(false);
    }
  };

  if (loading && !tree) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (error || !tree) {
    return (
      <Box
        sx={{ ...pageContentSx, display: 'flex', alignItems: 'center', gap: 1 }}
      >
        <Error />
        <Typography>Failed to load space</Typography>
      </Box>
    );
  }

  // Selection is URL-driven so member reports are deep-linkable and browser
  // back works; falling back to the overview is a route default, not state.
  // The API has already blanked the pointer if it no longer resolves.
  const activeReportId = reportId ?? tree.space.overview_report_id ?? undefined;
  const inSpace =
    activeReportId !== undefined &&
    tree.reports.some((report) => report.report_id === activeReportId);

  function mainRegion() {
    if (activeReportId === undefined) {
      // Nothing pinned. Name the action rather than silently picking a report,
      // which would be hard to explain the day it changes.
      return (
        <Box sx={pageContentSx}>
          <Typography variant="h1" sx={{ mb: 1 }}>
            {tree!.space.name}
          </Typography>
          {/* No button here: the sidebar footer already carries New report. */}
          <Typography color="text.secondary">
            {tree!.reports.length > 0
              ? "Set a report as this space's overview to show it here. Use the star action next to any report in the sidebar."
              : 'This space has no reports yet. Create one from the sidebar, or move an existing report in from the reports list.'}
          </Typography>
        </Box>
      );
    }
    if (!inSpace) {
      // Not silently redirected: a stale link should say so rather than
      // quietly showing a different report.
      return (
        <Box sx={pageContentSx}>
          <Typography variant="h1" sx={{ mb: 1 }}>
            Report not in this space
          </Typography>
          <Typography color="text.secondary">
            This report is not filed in {tree!.space.name}. It may have been
            moved, deleted, or you may not have access to it.
          </Typography>
        </Box>
      );
    }
    return (
      <ReportPane
        // Same reason as the standalone report route: never carry one report's
        // editor state into another.
        key={activeReportId}
        id={activeReportId}
        reportPath={reportPath}
        stickyToolbar={false}
      />
    );
  }

  return (
    <>
      <Helmet>
        <title>{tree.space.name} | Seizu</title>
      </Helmet>
      {/* Bounded height, matching ChatInterface: without it the panel grows with
          its content and scrolls with the page, sliding its header up under the
          fixed navbar. */}
      <Box
        sx={{
          display: 'flex',
          height: `calc(100vh - ${DASHBOARD_NAVBAR_HEIGHT}px)`,
          overflow: 'hidden',
        }}
      >
        <SpaceReportsPanel
          open={panelOpen}
          onToggle={() => setPanelOpen((prev) => !prev)}
          tree={tree}
          // Not activeReportId: landing on the space root renders the
          // overview, but highlighting its row would make the star redundant
          // and imply the user had picked it.
          selectedReportId={reportId}
          canWriteSpaces={canWriteSpaces}
          canDeleteSpaces={canDeleteSpaces}
          canWriteReports={canWriteReports}
          onSelectReport={(targetReportId) =>
            navigate(reportPath(targetReportId))
          }
          onCreateSubspace={createSubspace}
          onRenameSubspace={updateSubspace}
          onDeleteSubspace={deleteSubspace}
          onSetReportSubspace={handleSetReportSubspace}
          onRemoveReportFromSpace={handleRemoveReportFromSpace}
          onSetOverview={handleSetOverview}
          onCreateReport={() => {
            setCreateError(null);
            setCreateOpen(true);
          }}
        />

        <Box sx={{ flexGrow: 1, minWidth: 0, overflow: 'auto' }}>
          {mainRegion()}
        </Box>
      </Box>

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
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreateReport()}
            sx={{ mt: 1 }}
          />
          {/* Not a draft, unlike New report elsewhere: reports in a space are
              public, so the create publishes. Say so rather than let the user
              discover it from a disabled Unpublish action. */}
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
            disabled={creating || !newName.trim()}
          >
            {creating ? <ConstellationSpinner size={20} /> : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

export default SpaceDetail;
