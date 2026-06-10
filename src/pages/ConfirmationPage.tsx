import { useCallback, useContext, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, Paper, Typography } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import CheckCircle from '@mui/icons-material/CheckCircle';
import Block from '@mui/icons-material/Block';
import { AuthConfigContext } from 'src/authConfig.context';
import { AuthContext } from 'src/auth.context';
import {
  type ActionConfirmation,
  effectiveConfirmationStatus,
  useConfirmationsApi,
} from 'src/hooks/useConfirmationsApi';
import { pageContentSx } from 'src/theme/layout';

export default function ConfirmationPage() {
  const navigate = useNavigate();
  const { confirmationId } = useParams<{ confirmationId: string }>();
  const { auth_required } = useContext(AuthConfigContext);
  const { accessToken } = useContext(AuthContext);
  const waitingForToken = auth_required && !accessToken;
  const { getConfirmation, decideConfirmation } = useConfirmationsApi(null);
  const [confirmation, setConfirmation] = useState<ActionConfirmation | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<'approved' | 'denied' | null>(null);

  useEffect(() => {
    if (!confirmationId || waitingForToken) return;
    let cancelled = false;
    setLoading(true);
    void getConfirmation(confirmationId)
      .then((item) => {
        if (!cancelled) {
          setConfirmation(item);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError('Confirmation not found.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [confirmationId, getConfirmation, waitingForToken]);

  const decide = useCallback(
    async (decision: 'approved' | 'denied') => {
      if (!confirmationId) return;
      setDeciding(decision);
      try {
        const updated = await decideConfirmation(confirmationId, decision);
        setConfirmation(updated);
        setError(null);
        if (decision === 'approved' && updated.thread_id) {
          const params = new URLSearchParams({
            resume_confirmation_id: updated.confirmation_id,
          });
          navigate(
            `/app/chat/${encodeURIComponent(updated.thread_id)}?${params.toString()}`,
          );
        }
      } catch {
        setError('Failed to update confirmation.');
      } finally {
        setDeciding(null);
      }
    },
    [confirmationId, decideConfirmation, navigate],
  );

  if (waitingForToken || loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  return (
    <Box sx={{ ...pageContentSx, maxWidth: 760 }}>
      <Typography variant="h1" sx={{ mb: 2 }}>
        Action confirmation
      </Typography>
      {error ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      ) : null}
      {confirmation
        ? (() => {
            const status = effectiveConfirmationStatus(confirmation);
            const isExpired = status === 'expired';
            return (
              <Paper sx={{ p: 2 }} variant="outlined">
                <Typography variant="h6">
                  {confirmation.action} {confirmation.resource_type}
                </Typography>
                <Typography
                  color="text.secondary"
                  sx={{ wordBreak: 'break-word' }}
                  variant="body2"
                >
                  {confirmation.resource_id}
                </Typography>
                <Typography sx={{ mt: 2 }} variant="subtitle2">
                  Tool
                </Typography>
                <Typography sx={{ wordBreak: 'break-word' }} variant="body2">
                  {confirmation.tool_name}
                </Typography>
                <Typography sx={{ mt: 2 }} variant="subtitle2">
                  Arguments
                </Typography>
                <Typography
                  component="pre"
                  sx={{
                    bgcolor: 'action.hover',
                    borderRadius: 1,
                    fontFamily: '"JetBrains Mono", monospace',
                    fontSize: '0.8rem',
                    maxHeight: 360,
                    overflow: 'auto',
                    p: 1.25,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {JSON.stringify(confirmation.arguments, null, 2)}
                </Typography>
                <Typography
                  color="text.secondary"
                  sx={{ mt: 1 }}
                  variant="body2"
                >
                  Status: {status}
                </Typography>
                {isExpired ? (
                  <Alert severity="warning" sx={{ mt: 2 }}>
                    This confirmation has expired and can no longer be approved.
                    Re-run the action to get a fresh confirmation.
                  </Alert>
                ) : null}
                {status === 'pending' ? (
                  <Box sx={{ display: 'flex', gap: 1, mt: 2 }}>
                    <Button
                      disabled={deciding !== null}
                      onClick={() => void decide('approved')}
                      startIcon={<CheckCircle />}
                      variant="contained"
                    >
                      Allow
                    </Button>
                    <Button
                      color="error"
                      disabled={deciding !== null}
                      onClick={() => void decide('denied')}
                      startIcon={<Block />}
                      variant="outlined"
                    >
                      Deny
                    </Button>
                  </Box>
                ) : null}
              </Paper>
            );
          })()
        : null}
    </Box>
  );
}
