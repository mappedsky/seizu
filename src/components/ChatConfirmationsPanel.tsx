import {
  Alert,
  Badge,
  Box,
  Button,
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material';
import CheckCircle from '@mui/icons-material/CheckCircle';
import Close from '@mui/icons-material/Close';
import GppMaybe from '@mui/icons-material/GppMaybe';
import MenuOpen from '@mui/icons-material/MenuOpen';
import Block from '@mui/icons-material/Block';
import type { ActionConfirmation } from 'src/hooks/useConfirmationsApi';

interface ChatConfirmationsPanelProps {
  confirmations: ActionConfirmation[];
  loading: boolean;
  error: string | null;
  open: boolean;
  decidingId: string | null;
  onToggle: () => void;
  onDecision: (
    confirmation: ActionConfirmation,
    decision: 'approved' | 'denied',
  ) => void;
}

export default function ChatConfirmationsPanel({
  confirmations = [],
  loading,
  error,
  open,
  decidingId,
  onToggle,
  onDecision,
}: ChatConfirmationsPanelProps) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down('lg'));
  const pendingCount = confirmations.length;
  const openLabel =
    pendingCount > 0
      ? `Open confirmations (${pendingCount} pending)`
      : 'Open confirmations';
  const content = (
    <Box
      sx={{
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        width: { xs: 320, lg: 300 },
      }}
    >
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          flexShrink: 0,
          gap: 1,
          px: 1.5,
          py: 1,
        }}
      >
        <GppMaybe color="action" fontSize="small" />
        <Typography sx={{ flex: 1 }} variant="subtitle2">
          Confirmations
        </Typography>
        <IconButton
          aria-label={narrow ? 'Close confirmations' : 'Collapse confirmations'}
          onClick={onToggle}
          size="small"
        >
          <Close fontSize="small" />
        </IconButton>
      </Box>
      <Divider />
      <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', p: 1.5 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={22} />
          </Box>
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : confirmations.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            No pending approvals.
          </Typography>
        ) : (
          confirmations.map((confirmation) => (
            <Box
              key={confirmation.confirmation_id}
              sx={{
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                mb: 1.25,
                p: 1.25,
              }}
            >
              <Typography
                variant="subtitle2"
                sx={{ textTransform: 'capitalize', wordBreak: 'break-word' }}
              >
                {confirmation.action} {confirmation.resource_type}
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
                    {Object.entries(confirmation.arguments).map(
                      ([key, value]) => (
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
                              fontFamily: '"JetBrains Mono", monospace',
                              fontSize: '0.68rem',
                              lineHeight: 1.5,
                              minWidth: 72,
                              wordBreak: 'break-all',
                            }}
                          >
                            {key}
                          </Typography>
                          <Typography
                            sx={{
                              fontFamily: '"JetBrains Mono", monospace',
                              fontSize: '0.68rem',
                              lineHeight: 1.5,
                              wordBreak: 'break-all',
                            }}
                          >
                            {typeof value === 'object' && value !== null
                              ? JSON.stringify(value)
                              : String(value ?? '—')}
                          </Typography>
                        </Box>
                      ),
                    )}
                  </Box>
                </Box>
              )}
              <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
                <Button
                  disabled={decidingId === confirmation.confirmation_id}
                  onClick={() => onDecision(confirmation, 'approved')}
                  size="small"
                  startIcon={<CheckCircle />}
                  variant="contained"
                >
                  Allow
                </Button>
                <Button
                  color="error"
                  disabled={decidingId === confirmation.confirmation_id}
                  onClick={() => onDecision(confirmation, 'denied')}
                  size="small"
                  startIcon={<Block />}
                  variant="outlined"
                >
                  Deny
                </Button>
              </Box>
            </Box>
          ))
        )}
      </Box>
    </Box>
  );

  if (narrow) {
    return (
      <>
        <Tooltip title="Confirmations">
          <Badge
            badgeContent={pendingCount}
            color="error"
            invisible={pendingCount === 0}
            max={99}
            sx={{ bottom: 16, position: 'fixed', right: 16, zIndex: 1200 }}
          >
            <IconButton aria-label={openLabel} onClick={onToggle}>
              <MenuOpen />
            </IconButton>
          </Badge>
        </Tooltip>
        <Drawer anchor="right" open={open} onClose={onToggle}>
          {content}
        </Drawer>
      </>
    );
  }

  if (!open) {
    return (
      <Box
        component="aside"
        sx={{
          alignItems: 'flex-start',
          borderLeft: 1,
          borderColor: 'divider',
          display: 'flex',
          flexShrink: 0,
          height: '100%',
          justifyContent: 'center',
          px: 1,
          py: 1,
          width: 56,
        }}
      >
        <Tooltip title="Confirmations">
          <Badge
            badgeContent={pendingCount}
            color="error"
            invisible={pendingCount === 0}
            max={99}
          >
            <IconButton aria-label={openLabel} onClick={onToggle}>
              <GppMaybe />
            </IconButton>
          </Badge>
        </Tooltip>
      </Box>
    );
  }

  return (
    <Box
      component="aside"
      sx={{
        borderLeft: 1,
        borderColor: 'divider',
        display: 'flex',
        flexShrink: 0,
        height: '100%',
      }}
    >
      {content}
    </Box>
  );
}
