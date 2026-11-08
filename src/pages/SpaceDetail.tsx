import { useCallback, useState, type ComponentType } from 'react';
import PageTitle from 'src/components/PageTitle';
import { useSidePanel } from 'src/hooks/useSidePanel';
import { useNavigate, useParams } from 'react-router-dom';
import { Box, Button, Typography } from '@mui/material';
import Error from '@mui/icons-material/Error';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ReportPane, { type ReportPaneProps } from 'src/components/ReportPane';
import SpaceReportsPanel from 'src/components/SpaceReportsPanel';
import SpaceContents from 'src/components/spaces/SpaceContents';
import { useSpaceManagement } from 'src/components/spaces/useSpaceManagement';
import { SpaceDndProvider } from 'src/components/spaces/spaceDnd';
import { useSpaceTree } from 'src/hooks/useSpacesApi';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';
import { pageContentSx } from 'src/theme/layout';

interface SpaceDetailProps {
  /** `contents` forces the space's own page even when an overview is pinned. */
  view?: 'default' | 'contents';
  ReportPaneComponent?: ComponentType<ReportPaneProps>;
}

function SpaceDetail({
  view = 'default',
  ReportPaneComponent = ReportPane,
}: SpaceDetailProps) {
  const { spaceId, reportId } = useParams();
  const navigate = useNavigate();
  const [panelOpen, setPanelOpen] = useState(true);
  const reportsPanel = useSidePanel(panelOpen, setPanelOpen);

  // No explicit refresh wiring here: every mutation broadcasts an invalidation
  // (sub-spaces and the overview pointer via the spaces signal, membership via
  // the reports signal) that useSpaceTree subscribes to.
  const { tree: fetched, loading, error } = useSpaceTree(spaceId ?? null);
  const manage = useSpaceManagement(fetched, reportId);
  // Drawn with in-flight moves applied, so a drop lands before the reload.
  const tree = manage.view;

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
  // back works; falling back to the overview is a route default, not state.
  // The API has already blanked the pointer if it no longer resolves.
  const activeReportId =
    view === 'contents'
      ? undefined
      : (reportId ?? tree.space.overview_report_id ?? undefined);
  const showingContents = activeReportId === undefined;
  const inSpace =
    activeReportId !== undefined &&
    tree.reports.some((report) => report.report_id === activeReportId);

  function mainRegion() {
    if (showingContents) {
      return (
        <SpaceContents tree={tree!} manage={manage} reportPath={reportPath} />
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
          <Typography color="text.secondary" sx={{ mb: 2 }}>
            This report is not filed in {tree!.space.name}. It may have been
            moved, deleted, or you may not have access to it.
          </Typography>
          <Button
            variant="outlined"
            onClick={() => navigate(`/app/spaces/${spaceId}/contents`)}
          >
            See what is in {tree!.space.name}
          </Button>
        </Box>
      );
    }
    return (
      <ReportPaneComponent
        // Same reason as the standalone report route: never carry one report's
        // editor state into another.
        key={activeReportId}
        id={activeReportId}
        reportPath={reportPath}
        stickyToolbar={false}
        onSpaceChanged={(targetSpaceId) => {
          // Moved out of this space: follow it, to its new space or to the
          // standalone report route.
          if (targetSpaceId === spaceId) return;
          navigate(
            targetSpaceId
              ? `/app/spaces/${targetSpaceId}/reports/${activeReportId}`
              : `/app/reports/${activeReportId}`,
            { replace: true },
          );
        }}
      />
    );
  }

  return (
    <>
      {/* The report pane names the tab when a report is open, and only one
          PageTitle may be mounted at a time. */}
      {!inSpace && <PageTitle>{tree.space.name} | Seizu</PageTitle>}
      {/* Bounded height, matching ChatInterface: without it the panel grows with
          its content and scrolls with the page, sliding its header up under the
          fixed navbar. */}
      <SpaceDndProvider
        onMove={(reports, subspaceId) =>
          void manage.moveReports(reports, subspaceId)
        }
        onReorder={manage.reorderSubspace}
        enabled={
          (manage.canWriteReports || manage.canWriteSpaces) && !manage.moving
        }
      >
        <Box
          sx={{
            display: 'flex',
            height: `calc(100vh - ${DASHBOARD_NAVBAR_HEIGHT}px)`,
            overflow: 'hidden',
          }}
        >
          <SpaceReportsPanel
            open={reportsPanel.open}
            overlay={reportsPanel.overlay}
            onToggle={reportsPanel.toggle}
            tree={tree}
            // Not activeReportId: landing on the space root renders the
            // overview, but highlighting its row would make the star redundant
            // and imply the user had picked it.
            selectedReportId={reportId}
            contentsSelected={showingContents}
            onSelectReport={(targetReportId) => {
              reportsPanel.dismiss();
              navigate(reportPath(targetReportId));
            }}
            onOpenContents={() => {
              reportsPanel.dismiss();
              navigate(`/app/spaces/${spaceId}/contents`);
            }}
            manage={manage}
          />

          <Box sx={{ flexGrow: 1, minWidth: 0, overflow: 'auto' }}>
            {mainRegion()}
          </Box>
        </Box>
      </SpaceDndProvider>

      {manage.dialogs}
    </>
  );
}

export default SpaceDetail;
