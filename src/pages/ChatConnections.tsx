import { Alert, Box, Button, Paper, Stack, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { useFeature } from 'src/features.context';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ConnectionStatus,
  useChatConnections,
} from 'src/hooks/useChatConnections';
import { pageContentSx } from 'src/theme/layout';

const labels: Record<ConnectionStatus, string> = {
  unknown: 'Not checked',
  connected: 'Connected',
  authorization_required: 'Reauthorization required',
  service_authentication_failed: 'Service authentication failed',
  permission_denied: 'Access denied',
  unavailable: 'Unavailable',
};

export default function ChatConnections() {
  const enabled = useFeature('chat');
  const permissions = usePermissionState();
  const allowed =
    enabled && !permissions.loading && permissions.hasPermission('chat:use');
  const { connections, loading, error, checking, check } =
    useChatConnections(allowed);

  if (permissions.loading) return <ConstellationSpinner />;
  if (!allowed)
    return <Alert severity="warning">Chat connections are unavailable.</Alert>;

  return (
    <Box sx={pageContentSx}>
      <Button component={RouterLink} to="/app/chat">
        Back to Chat
      </Button>
      <Typography variant="h1" sx={{ mb: 2 }}>
        Chat connections
      </Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        Your accounts connected through external gateways. Status reflects the
        latest check or request; individual tools may require additional
        permissions.
      </Typography>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {loading ? (
        <ConstellationSpinner />
      ) : (
        <Stack spacing={2}>
          {!connections.length && (
            <Typography>No per-user gateways are configured.</Typography>
          )}
          {connections.map((connection) => (
            <Paper key={connection.proxy_name} sx={{ p: 3 }}>
              <Typography variant="h2">{connection.proxy_name}</Typography>
              <Typography sx={{ mt: 1 }}>
                {labels[connection.status]}
              </Typography>
              <Typography color="text.secondary" variant="body2">
                Last observed:{' '}
                {connection.observed_at
                  ? new Date(connection.observed_at).toLocaleString()
                  : 'Never'}
              </Typography>
              {connection.status === 'service_authentication_failed' && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  Ask an administrator to check Seizu’s gateway service
                  authentication.
                </Alert>
              )}
              {connection.status === 'permission_denied' && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  The gateway denied access. Check your upstream account’s
                  permissions.
                </Alert>
              )}
              {connection.status === 'authorization_required' && (
                <Typography sx={{ mt: 2 }}>
                  Reauthorize your account through the gateway, then check the
                  connection.
                </Typography>
              )}
              <Stack direction="row" spacing={2} sx={{ mt: 2 }}>
                {connection.status === 'authorization_required' &&
                  connection.reauthorize_url && (
                    <Button
                      component="a"
                      href={connection.reauthorize_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Reauthorize
                    </Button>
                  )}
                <Button
                  disabled={checking !== null}
                  onClick={() => void check(connection.proxy_name)}
                >
                  {checking === connection.proxy_name
                    ? 'Checking…'
                    : 'Check connection'}
                </Button>
              </Stack>
            </Paper>
          ))}
        </Stack>
      )}
    </Box>
  );
}
