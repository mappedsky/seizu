import { useCallback, useContext, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Paper,
  Typography,
} from '@mui/material';
import CheckCircle from '@mui/icons-material/CheckCircle';
import Block from '@mui/icons-material/Block';
import { AuthConfigContext } from 'src/authConfig.context';
import { AuthContext } from 'src/auth.context';
import {
  type ActionConfirmation,
  useConfirmationsApi,
} from 'src/hooks/useConfirmationsApi';
import { pageContentSx } from 'src/theme/layout';

function statusColor(
  status: ActionConfirmation['status'],
): 'default' | 'success' | 'error' | 'warning' {
  if (status === 'approved' || status === 'executed') return 'success';
  if (status === 'denied') return 'error';
  if (status === 'expired') return 'warning';
  return 'default';
}

function ConfirmationCard({
  confirmation,
  onDecide,
  deciding,
}: {
  confirmation: ActionConfirmation;
  onDecide: (id: string, decision: 'approved' | 'denied') => void;
  deciding: string | null;
}) {
  const isPending = confirmation.status === 'pending';
  const isDeciding = deciding === confirmation.confirmation_id;

  return (
    <Paper sx={{ p: 2 }} variant="outlined">
      <Box sx={{ alignItems: 'center', display: 'flex', gap: 1, mb: 1 }}>
        <Typography sx={{ flexGrow: 1 }} variant="h6">
          {confirmation.action} {confirmation.resource_type}
        </Typography>
        <Chip
          color={statusColor(confirmation.status)}
          label={confirmation.status}
          size="small"
        />
      </Box>
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
      {Object.keys(confirmation.arguments).length > 0 ? (
        <>
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
              maxHeight: 240,
              overflow: 'auto',
              p: 1.25,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {JSON.stringify(confirmation.arguments, null, 2)}
          </Typography>
        </>
      ) : null}
      {isPending ? (
        <Box sx={{ display: 'flex', gap: 1, mt: 2 }}>
          <Button
            disabled={deciding !== null}
            onClick={() => onDecide(confirmation.confirmation_id, 'approved')}
            startIcon={
              isDeciding && deciding === confirmation.confirmation_id ? (
                <CircularProgress size={16} />
              ) : (
                <CheckCircle />
              )
            }
            variant="contained"
          >
            Allow
          </Button>
          <Button
            color="error"
            disabled={deciding !== null}
            onClick={() => onDecide(confirmation.confirmation_id, 'denied')}
            startIcon={<Block />}
            variant="outlined"
          >
            Deny
          </Button>
        </Box>
      ) : null}
    </Paper>
  );
}

export default function BatchConfirmationPage() {
  const navigate = useNavigate();
  const { batchId } = useParams<{ batchId: string }>();
  const { auth_required } = useContext(AuthConfigContext);
  const { accessToken } = useContext(AuthContext);
  const waitingForToken = auth_required && !accessToken;
  const { getConfirmationsByBatchId, decideConfirmation } =
    useConfirmationsApi(null);
  const [confirmations, setConfirmations] = useState<ActionConfirmation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!batchId || waitingForToken) return;
    void getConfirmationsByBatchId(batchId)
      .then((items) => {
        setConfirmations(items);
        setError(null);
      })
      .catch(() => {
        setError('Failed to load confirmations.');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [batchId, getConfirmationsByBatchId, waitingForToken]);

  useEffect(() => {
    if (waitingForToken) return;
    setLoading(true);
    reload();
  }, [reload, waitingForToken]);

  const handleDecide = useCallback(
    async (confirmationId: string, decision: 'approved' | 'denied') => {
      setDeciding(confirmationId);
      try {
        const updated = await decideConfirmation(confirmationId, decision);
        const nextConfirmations = confirmations.map((c) =>
          c.confirmation_id === confirmationId ? updated : c,
        );
        setConfirmations(nextConfirmations);
        setError(null);
        const remainingPending = nextConfirmations.filter(
          (c) => c.status === 'pending',
        ).length;
        const hasBlockedDecision = nextConfirmations.some(
          (c) => c.status === 'denied' || c.status === 'expired',
        );
        if (
          decision === 'approved' &&
          remainingPending === 0 &&
          !hasBlockedDecision &&
          updated.thread_id
        ) {
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
    [confirmations, decideConfirmation, navigate],
  );

  const pendingCount = confirmations.filter(
    (c) => c.status === 'pending',
  ).length;

  if (waitingForToken || loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ ...pageContentSx, maxWidth: 760 }}>
      <Typography variant="h1" sx={{ mb: 1 }}>
        Action confirmations
      </Typography>
      {confirmations.length > 0 ? (
        <Typography color="text.secondary" sx={{ mb: 3 }} variant="body2">
          {pendingCount > 0
            ? `${pendingCount} of ${confirmations.length} action${confirmations.length !== 1 ? 's' : ''} pending approval`
            : `All ${confirmations.length} action${confirmations.length !== 1 ? 's' : ''} have been decided`}
        </Typography>
      ) : null}
      {error ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      ) : null}
      {confirmations.length === 0 && !error ? (
        <Alert severity="info">No confirmations found for this batch.</Alert>
      ) : null}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {confirmations.map((confirmation, index) => (
          <Box key={confirmation.confirmation_id}>
            {index > 0 ? <Divider sx={{ mb: 2 }} /> : null}
            <ConfirmationCard
              confirmation={confirmation}
              deciding={deciding}
              onDecide={(id, decision) => void handleDecide(id, decision)}
            />
          </Box>
        ))}
      </Box>
    </Box>
  );
}
