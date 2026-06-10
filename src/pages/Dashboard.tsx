import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import RefreshIcon from '@mui/icons-material/Refresh';

import ReportView from 'src/components/ReportView';
import { useDashboardReport } from 'src/hooks/useReportsApi';
import { pageContentSx } from 'src/theme/layout';

function Dashboard() {
  const { report, queryCapabilities, loading, refresh } = useDashboardReport();

  if (loading || (report && queryCapabilities === undefined)) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (!report) {
    return (
      <Box sx={pageContentSx}>
        <Typography>No dashboard configured.</Typography>
      </Box>
    );
  }

  return (
    <ReportView
      report={report}
      title="Dashboard"
      queryCapabilities={queryCapabilities}
      boxSx={{
        backgroundColor: 'background.default',
        minHeight: '100%',
        py: 3,
      }}
      toolbarActions={({ onRefresh, refreshedAtLabel }) => (
        <Tooltip title={refreshedAtLabel ?? 'Refresh data'}>
          <IconButton
            size="small"
            onClick={onRefresh}
            aria-label="Refresh data"
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      onRefreshCapabilities={refresh}
    />
  );
}

export default Dashboard;
