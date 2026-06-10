import { useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  Alert,
  Box,
  Button,
  Divider,
  Tooltip,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import NavigateBeforeIcon from '@mui/icons-material/NavigateBefore';
import NavigateNextIcon from '@mui/icons-material/NavigateNext';
import RestoreIcon from '@mui/icons-material/Restore';
import Error from '@mui/icons-material/Error';

import ReportView from 'src/components/ReportView';
import UserDisplay from 'src/components/UserDisplay';
import {
  useReportVersion,
  useReportVersionsList,
  useReportsMutations,
} from 'src/hooks/useReportsApi';
import { Report } from 'src/config.context';
import { usePermissionState } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { contentContainerSx, pageContentSx } from 'src/theme/layout';

function ReportVersionView() {
  const { id, version } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { fromLabel } = (location.state ?? {}) as BackState;
  const backState = (location.state ?? {}) as BackState;
  const returnTo = backState.returnTo;

  const { reportVersion, loading, error } = useReportVersion(id, version);
  const { versions } = useReportVersionsList(id);
  const { saveReportVersion } = useReportsMutations();

  // Build sorted version number list (ascending) to find neighbours
  const sortedVersionNums = [...versions]
    .map((v) => v.version)
    .sort((a, b) => a - b);
  const currentVersionNum = reportVersion?.version ?? null;
  const currentIdx =
    currentVersionNum !== null
      ? sortedVersionNums.indexOf(currentVersionNum)
      : -1;
  const prevVersion = currentIdx > 0 ? sortedVersionNums[currentIdx - 1] : null;
  const nextVersion =
    currentIdx !== -1 && currentIdx < sortedVersionNums.length - 1
      ? sortedVersionNums[currentIdx + 1]
      : null;

  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canWrite = hasPermission('reports:write');

  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);

  async function handleRestore() {
    if (!id || !reportVersion) return;
    setRestoring(true);
    setRestoreError(null);
    try {
      await saveReportVersion(
        id,
        reportVersion.config as Report,
        `Restored from version ${reportVersion.version}`,
      );
      navigate(`/app/reports/${id}`);
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setRestoreError((err as any)?.message ?? 'Failed to restore version');
      setRestoring(false);
    }
  }

  if (
    loading ||
    permissionsLoading ||
    (reportVersion && reportVersion.query_capabilities === undefined)
  ) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (error || !reportVersion) {
    return (
      <Box
        sx={{ ...pageContentSx, display: 'flex', alignItems: 'center', gap: 1 }}
      >
        <Error />
        <Typography>Failed to load this version</Typography>
      </Box>
    );
  }

  return (
    <>
      <Helmet>
        <title>{`v${reportVersion.version} – ${reportVersion.name} | Seizu`}</title>
      </Helmet>

      {/* Top action bar */}
      <Box
        sx={{
          position: 'sticky',
          top: 0,
          zIndex: 100,
          bgcolor: 'background.paper',
          borderBottom: 1,
          borderColor: 'divider',
          ...contentContainerSx,
          py: 1.5,
          display: 'flex',
          alignItems: 'center',
          gap: 2,
        }}
      >
        {fromLabel && (
          <>
            <Button
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={() => {
                if (returnTo) {
                  navigate(returnTo, { state: backState });
                  return;
                }
                navigate(-1);
              }}
            >
              Back to {fromLabel}
            </Button>
            <Divider orientation="vertical" flexItem />
          </>
        )}

        <Tooltip title={prevVersion === null ? 'No older version' : ''}>
          <span>
            <Button
              size="small"
              startIcon={<NavigateBeforeIcon />}
              disabled={prevVersion === null}
              onClick={() =>
                navigate(`/app/reports/${id}/versions/${prevVersion}`, {
                  state: backState,
                })
              }
            >
              {prevVersion !== null ? `v${prevVersion}` : 'Older'}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title={nextVersion === null ? 'No newer version' : ''}>
          <span>
            <Button
              size="small"
              endIcon={<NavigateNextIcon />}
              disabled={nextVersion === null}
              onClick={() =>
                navigate(`/app/reports/${id}/versions/${nextVersion}`, {
                  state: backState,
                })
              }
            >
              {nextVersion !== null ? `v${nextVersion}` : 'Newer'}
            </Button>
          </span>
        </Tooltip>

        <Divider orientation="vertical" flexItem />

        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="subtitle2" color="text.secondary">
            {`Viewing version ${currentIdx + 1} of ${sortedVersionNums.length}`}
          </Typography>
          <Typography variant="body2">
            <strong>v{reportVersion.version}</strong>
            {' · '}
            {new Date(reportVersion.created_at).toLocaleString()}
            {' · '}
            <UserDisplay userId={reportVersion.created_by} />
            {reportVersion.comment && ` · "${reportVersion.comment}"`}
          </Typography>
        </Box>

        {restoreError && (
          <Alert severity="error" sx={{ py: 0 }}>
            {restoreError}
          </Alert>
        )}

        <Tooltip
          title={
            nextVersion === null
              ? 'This is already the current version'
              : !canWrite
                ? 'You do not have permission to restore report versions'
                : ''
          }
        >
          <span>
            <Button
              variant="contained"
              startIcon={
                restoring ? <ConstellationSpinner size={16} /> : <RestoreIcon />
              }
              onClick={handleRestore}
              disabled={restoring || nextVersion === null || !canWrite}
            >
              Restore this version
            </Button>
          </span>
        </Tooltip>
      </Box>

      {/* Report content */}
      <ReportView
        report={reportVersion.config as Report}
        title={reportVersion.name}
        showTitle
        queryCapabilities={reportVersion.query_capabilities}
        stickyToolbar={false}
      />
    </>
  );
}

export default ReportVersionView;
