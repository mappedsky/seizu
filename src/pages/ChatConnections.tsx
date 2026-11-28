import { Alert, Box, Button, Paper, Stack, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { useFeature } from 'src/features.context';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ConnectionStatus,
  useChatConnections,
} from 'src/hooks/useChatConnections';
import { pageContentSx } from 'src/theme/layout';
import { CHAT_RETURN_PARAM, chatReturnPath } from 'src/utils/chatPaths';

// Why a connection attempt came back unsuccessful, keyed by the callback's
// connect_error value.
const connectErrors: Record<string, string> = {
  denied: 'You declined the authorization, so nothing was connected.',
  connect_expired:
    'The connection attempt expired or was started in another browser. Try again.',
  state_mismatch:
    'That authorization did not match this browser’s connection attempt. Try again.',
  no_refresh_token:
    'The gateway did not grant offline access, so Seizu could not keep your connection.',
  unavailable: 'The gateway is unavailable. Try again later.',
};

const labels: Record<ConnectionStatus, string> = {
  unknown: 'Not checked',
  connected: 'Connected',
  authorization_required: 'Reauthorization required',
  interaction_required: 'User interaction required',
  service_authentication_failed: 'Service authentication failed',
  permission_denied: 'Access denied',
  unavailable: 'Unavailable',
};

export default function ChatConnections() {
  const [searchParams] = useSearchParams();
  // Back to the conversation this was opened from, and only to the landing
  // when it was reached some other way.
  const backTo = chatReturnPath(searchParams.get(CHAT_RETURN_PARAM));
  const chatEnabled = useFeature('chat');
  const connectionsEnabled = useFeature('chat_connections');
  const enabled = chatEnabled && connectionsEnabled;
  const permissions = usePermissionState();
  const allowed =
    enabled && !permissions.loading && permissions.hasPermission('chat:use');
  const { connections, loading, error, checking, check, connect, disconnect } =
    useChatConnections(allowed);
  const connected = searchParams.get('connected');
  const connectError = searchParams.get('connect_error');

  if (permissions.loading) return <ConstellationSpinner />;
  if (!allowed)
    return <Alert severity="warning">Chat connections are unavailable.</Alert>;

  return (
    <Box sx={pageContentSx}>
      <Button
        component={RouterLink}
        to={backTo}
        size="small"
        startIcon={<ArrowBackIcon />}
        sx={{ mb: 1 }}
      >
        Back to chat
      </Button>
      <Typography variant="h1" sx={{ mb: 2 }}>
        Chat connections
      </Typography>
      <Alert severity="warning" sx={{ mb: 2 }}>
        Experimental: per-user gateway delegation and recovery. Administrators
        must validate the gateway’s identity mapping and per-user access before
        relying on it. A connected status does not verify user isolation.
      </Alert>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        Your accounts connected through external gateways. Status reflects the
        latest check or request; individual tools may require additional
        permissions.
      </Typography>
      {connected && !connectError && (
        <Alert severity="success" sx={{ mb: 2 }}>
          Connected {connected}. Check the connection to confirm access.
        </Alert>
      )}
      {connectError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {connectErrors[connectError] ??
            'Connecting failed. Try again, or ask an administrator.'}
        </Alert>
      )}
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
                {connection.connectable && !connection.has_grant
                  ? 'Not connected'
                  : labels[connection.status]}
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
              {connection.status === 'authorization_required' &&
                !connection.connectable && (
                  <Typography sx={{ mt: 2 }}>
                    Reauthorize your account through the gateway, then check the
                    connection.
                  </Typography>
                )}
              {connection.connectable && !connection.has_grant && (
                <Typography sx={{ mt: 2 }}>
                  Connect your account so Seizu can use this gateway as you,
                  including in scheduled runs.
                </Typography>
              )}
              {connection.connectable &&
                connection.has_grant &&
                connection.status === 'authorization_required' && (
                  <Typography sx={{ mt: 2 }}>
                    The gateway no longer accepts your connection, or one of its
                    services needs your consent. Connect again.
                  </Typography>
                )}
              <Stack direction="row" spacing={2} sx={{ mt: 2 }}>
                {connection.status === 'interaction_required' && (
                  <Stack spacing={1}>
                    <Typography>
                      The gateway requested an external interaction. Review the
                      destination before opening it. Nothing has been accepted
                      automatically. After completing it, retry your request.
                    </Typography>
                    {(connection.elicitations ?? []).map(
                      (elicitation, index) => (
                        <Stack key={index}>
                          <Typography>{elicitation.message}</Typography>
                          <Button
                            component="a"
                            href={elicitation.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Open {new URL(elicitation.url).host}
                          </Button>
                        </Stack>
                      ),
                    )}
                    {!connection.elicitations?.length && (
                      <Typography>
                        No current approved link is available. Check the
                        connection or retry the request to obtain a fresh link.
                      </Typography>
                    )}
                    {connection.reauthorize_url && (
                      <Button
                        component="a"
                        href={connection.reauthorize_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Account page ({new URL(connection.reauthorize_url).host}
                        )
                      </Button>
                    )}
                  </Stack>
                )}
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
                {connection.connectable && (
                  <Button
                    variant={connection.has_grant ? 'text' : 'contained'}
                    disabled={checking !== null}
                    onClick={() => void connect(connection.proxy_name)}
                  >
                    {connection.has_grant ? 'Reconnect' : 'Connect'}
                  </Button>
                )}
                {(!connection.connectable || connection.has_grant) && (
                  <Button
                    disabled={checking !== null}
                    onClick={() => void check(connection.proxy_name)}
                  >
                    {checking === connection.proxy_name
                      ? 'Checking…'
                      : 'Check connection'}
                  </Button>
                )}
                {connection.connectable && connection.has_grant && (
                  <Button
                    color="error"
                    disabled={checking !== null}
                    onClick={() => void disconnect(connection.proxy_name)}
                  >
                    Disconnect
                  </Button>
                )}
              </Stack>
            </Paper>
          ))}
        </Stack>
      )}
    </Box>
  );
}
