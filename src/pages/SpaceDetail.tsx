import { useCallback, useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate, useParams } from 'react-router-dom';
import { Box, Typography } from '@mui/material';
import Error from '@mui/icons-material/Error';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ReportPane from 'src/components/ReportPane';
import SpaceReportsPanel from 'src/components/SpaceReportsPanel';
import { useReportsMutations } from 'src/hooks/useReportsApi';
import { useSpaceTree, useSubspaceMutations } from 'src/hooks/useSpacesApi';
import { usePermissions } from 'src/hooks/usePermissions';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';
import { pageContentSx } from 'src/theme/layout';

function SpaceDetail() {
  const { spaceId, reportId } = useParams();
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const [panelOpen, setPanelOpen] = useState(true);

  // No explicit refresh wiring here: every mutation below broadcasts an
  // invalidation (sub-spaces via the spaces signal, report membership via the
  // reports signal) that useSpaceTree subscribes to, so adding refresh() calls
  // on top would fetch the tree twice per change.
  const { tree, loading, error } = useSpaceTree(spaceId ?? null);
  const { setReportSpace } = useReportsMutations();
  const { createSubspace, updateSubspace, deleteSubspace } =
    useSubspaceMutations(spaceId ?? '');

  const canWrite = hasPermission('spaces:write');
  const canDelete = hasPermission('spaces:delete');
  const canWriteReports = hasPermission('reports:write');

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
      // overview rather than leaving a report the space no longer contains.
      if (targetReportId === reportId && spaceId) {
        navigate(`/app/spaces/${spaceId}`, { replace: true });
      }
    },
    [setReportSpace, reportId, spaceId, navigate],
  );

  const reportPath = useCallback(
    (targetReportId: string) =>
      `/app/spaces/${spaceId}/reports/${targetReportId}`,
    [spaceId],
  );

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
  // back works; "overview by default" is a route default, not local state.
  const activeReportId = reportId ?? tree.space.overview_report_id;
  const inSpace =
    activeReportId === tree.space.overview_report_id ||
    tree.reports.some((report) => report.report_id === activeReportId);

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
          activeReportId={activeReportId}
          canWrite={canWrite}
          canDelete={canDelete}
          onSelectReport={(targetReportId) =>
            navigate(
              targetReportId === tree.space.overview_report_id
                ? `/app/spaces/${spaceId}`
                : reportPath(targetReportId),
            )
          }
          onCreateSubspace={createSubspace}
          onRenameSubspace={updateSubspace}
          onDeleteSubspace={deleteSubspace}
          onSetReportSubspace={handleSetReportSubspace}
          onRemoveReportFromSpace={
            canWriteReports ? handleRemoveReportFromSpace : async () => {}
          }
        />

        <Box sx={{ flexGrow: 1, minWidth: 0, overflow: 'auto' }}>
          {inSpace ? (
            // Not silently redirected: a stale link should say so rather than
            // quietly showing a different report.
            <ReportPane
              id={activeReportId}
              reportPath={reportPath}
              stickyToolbar={false}
            />
          ) : (
            <Box sx={{ ...pageContentSx }}>
              <Typography variant="h1" sx={{ mb: 1 }}>
                Report not in this space
              </Typography>
              <Typography color="text.secondary">
                This report is not filed in {tree.space.name}. It may have been
                moved, deleted, or you may not have access to it.
              </Typography>
            </Box>
          )}
        </Box>
      </Box>
    </>
  );
}

export default SpaceDetail;
