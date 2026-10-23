import { Box, Button, Typography } from '@mui/material';
import CheckCircle from '@mui/icons-material/CheckCircle';
import Block from '@mui/icons-material/Block';
import GppMaybe from '@mui/icons-material/GppMaybe';
import {
  effectiveConfirmationStatus,
  isConfirmationExpired,
  type ActionConfirmation,
} from 'src/hooks/useConfirmationsApi';
import { fontFamilyMono, fontSizeMin } from 'src/theme/typography';

interface ChatConfirmationCardProps {
  confirmation: ActionConfirmation;
  deciding: boolean;
  onDecision: (
    confirmation: ActionConfirmation,
    decision: 'approved' | 'denied',
  ) => void;
  /** In the transcript, under the turn that asked: leads with what it is
   *  waiting for, since nothing around it says so. */
  inline?: boolean;
}

/**
 * One action awaiting the user's decision: what it does, to what, with which
 * arguments, and Allow/Deny. A denied action that has not expired can still be
 * accepted. The same card serves the transcript and the confirmations panel.
 */
export default function ChatConfirmationCard({
  confirmation,
  deciding,
  onDecision,
  inline = false,
}: ChatConfirmationCardProps) {
  const status = effectiveConfirmationStatus(confirmation);
  const canReverseDenial =
    status === 'denied' && !isConfirmationExpired(confirmation);
  const title = `${confirmation.action} ${confirmation.resource_type}`;
  return (
    <Box
      component={inline ? 'section' : 'div'}
      aria-label={inline ? `Approval needed: ${title}` : undefined}
      sx={{
        border: 1,
        borderColor: inline ? 'warning.main' : 'divider',
        borderRadius: inline ? 2 : 1,
        mb: inline ? 1.5 : 1.25,
        p: inline ? 1.5 : 1.25,
      }}
    >
      {inline ? (
        <Box
          sx={{
            alignItems: 'center',
            color: 'warning.main',
            display: 'flex',
            gap: 0.75,
            mb: 0.75,
          }}
        >
          <GppMaybe fontSize="small" />
          <Typography sx={{ fontWeight: 600 }} variant="body2">
            Waiting for your approval
          </Typography>
        </Box>
      ) : null}
      <Typography
        variant="subtitle2"
        sx={{ textTransform: 'capitalize', wordBreak: 'break-word' }}
      >
        {title}
      </Typography>
      <Typography
        color="text.secondary"
        sx={{ display: 'block', wordBreak: 'break-word' }}
        variant="caption"
      >
        {confirmation.resource_id}
      </Typography>
      {Object.keys(confirmation.arguments).length > 0 && (
        <Box
          component="details"
          open
          sx={{
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            mt: 1,
            overflow: 'hidden',
          }}
        >
          <Box
            component="summary"
            sx={{
              cursor: 'pointer',
              fontSize: '0.72rem',
              fontWeight: 600,
              listStyle: 'revert',
              px: 1,
              py: 0.75,
              userSelect: 'none',
            }}
          >
            Request details
          </Box>
          <Box sx={{ borderTop: 1, borderColor: 'divider', p: 1 }}>
            {Object.entries(confirmation.arguments).map(([key, value]) => (
              <Box
                key={key}
                sx={{
                  alignItems: 'flex-start',
                  display: 'flex',
                  gap: 1,
                  '&:not(:last-child)': { mb: 0.5 },
                }}
              >
                <Typography
                  sx={{
                    color: 'text.secondary',
                    flexShrink: 0,
                    fontFamily: fontFamilyMono,
                    fontSize: fontSizeMin,
                    lineHeight: 1.5,
                    minWidth: 72,
                    wordBreak: 'break-all',
                  }}
                >
                  {key}
                </Typography>
                <Typography
                  sx={{
                    fontFamily: fontFamilyMono,
                    fontSize: fontSizeMin,
                    lineHeight: 1.5,
                    wordBreak: 'break-all',
                  }}
                >
                  {typeof value === 'object' && value !== null
                    ? JSON.stringify(value)
                    : String(value ?? '—')}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>
      )}
      {status === 'pending' ? (
        <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
          <Button
            disabled={deciding}
            onClick={() => onDecision(confirmation, 'approved')}
            size="small"
            startIcon={<CheckCircle />}
            variant="contained"
          >
            Allow
          </Button>
          <Button
            color="error"
            disabled={deciding}
            onClick={() => onDecision(confirmation, 'denied')}
            size="small"
            startIcon={<Block />}
            variant="outlined"
          >
            Deny
          </Button>
        </Box>
      ) : null}
      {canReverseDenial ? (
        <Box sx={{ mt: 1 }}>
          <Button
            color="error"
            disabled={deciding}
            onClick={() => onDecision(confirmation, 'approved')}
            size="small"
            variant="outlined"
          >
            Accept
          </Button>
        </Box>
      ) : null}
    </Box>
  );
}
