import { useContext, useEffect, useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, Paper, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import RestoreIcon from '@mui/icons-material/Restore';
import { AuthContext } from 'src/auth.context';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import {
  WorkflowRequest,
  useWorkflowMutations,
} from 'src/hooks/useWorkflowsApi';
import { pageContentSx } from 'src/theme/layout';

type Version = Omit<WorkflowRequest, 'comment'> & {
  workflow_id: string;
  version: number;
  created_at: string;
  created_by: string;
  comment: string | null;
};

export default function WorkflowHistory() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { accessToken } = useContext(AuthContext);
  const hasPermission = usePermissions();
  const { updateWorkflow } = useWorkflowMutations();
  const [versions, setVersions] = useState<Version[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);
  useEffect(() => {
    if (!id) return;
    fetch(`/api/v1/workflows/${encodeURIComponent(id)}/versions`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
      .then((response) => {
        if (!response.ok) throw new Error();
        return response.json() as Promise<{ versions: Version[] }>;
      })
      .then((data) => setVersions(data.versions ?? []))
      .catch(() => setError('Failed to load workflow versions.'));
  }, [accessToken, id]);
  const name = versions[0]?.name ?? 'Workflow';
  const currentVersion = Math.max(
    0,
    ...versions.map((version) => version.version),
  );
  return (
    <Box sx={pageContentSx}>
      <Helmet>
        <title>History – {name} | Seizu</title>
      </Helmet>
      <Button
        startIcon={<ArrowBackIcon />}
        onClick={() => navigate(`/app/workflows/${id}`)}
      >
        Back to workflow
      </Button>
      <Typography variant="h1" sx={{ my: 2 }}>
        Version history – {name}
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {versions.map((version) => (
        <Paper key={version.version} variant="outlined" sx={{ p: 2, mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="h6" sx={{ flex: 1 }}>
              Version {version.version}
              {version.version === currentVersion ? ' (current)' : ''}
            </Typography>
            <Button
              size="small"
              startIcon={<RestoreIcon />}
              disabled={
                version.version === currentVersion ||
                !hasPermission('workflows:write') ||
                restoring !== null
              }
              onClick={async () => {
                if (!id) return;
                setRestoring(version.version);
                setError(null);
                try {
                  await updateWorkflow(id, {
                    name: version.name,
                    stages: version.stages,
                    schedule: version.schedule,
                    watch_scans: version.watch_scans,
                    enabled: version.enabled,
                    comment: `Restored from version ${version.version}`,
                  });
                  navigate(`/app/workflows/${id}`);
                } catch {
                  setError('Failed to restore workflow version.');
                } finally {
                  setRestoring(null);
                }
              }}
            >
              {restoring === version.version ? 'Restoring…' : 'Restore'}
            </Button>
          </Box>
          <Typography variant="body2">
            Saved {new Date(version.created_at).toLocaleString()} by{' '}
            <UserDisplay userId={version.created_by} />
          </Typography>
          <Typography color="text.secondary">
            {version.comment ?? 'No comment'}
          </Typography>
          <Typography variant="body2">
            {version.stages.length} stage(s),{' '}
            {version.stages.reduce(
              (total, stage) => total + stage.activities.length,
              0,
            )}{' '}
            activity(ies)
          </Typography>
        </Paper>
      ))}
    </Box>
  );
}
