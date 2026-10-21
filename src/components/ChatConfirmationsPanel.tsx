import {
  Alert,
  Badge,
  Box,
  Button,
  Divider,
  Drawer,
  FormControlLabel,
  IconButton,
  Switch,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material';
import { memo, Fragment } from 'react';
import CheckCircle from '@mui/icons-material/CheckCircle';
import Close from '@mui/icons-material/Close';
import GppMaybe from '@mui/icons-material/GppMaybe';
import Block from '@mui/icons-material/Block';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import {
  effectiveConfirmationStatus,
  isConfirmationExpired,
  type ActionConfirmation,
} from 'src/hooks/useConfirmationsApi';
import { fontFamilyMono, fontSizeMin } from 'src/theme/typography';
import { SIDE_PANEL_RAIL_WIDTH } from 'src/components/SidePanelFrame';

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
  /** Whether the user may bypass confirmations at all; hides the setting
   *  when false. Plain values, not a node, so the panel stays memoized. */
  canBypassConfirmations?: boolean;
  bypassConfirmations?: boolean;
  onBypassConfirmationsChange?: (value: boolean) => void;
}

function ChatConfirmationsPanel({
  confirmations = [],
  loading,
  error,
  open,
  decidingId,
  onToggle,
  onDecision,
  canBypassConfirmations = false,
  bypassConfirmations = false,
  onBypassConfirmationsChange,
}: ChatConfirmationsPanelProps) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down('lg'));
  const pendingConfirmations = confirmations.filter(
    (confirmation) => effectiveConfirmationStatus(confirmation) === 'pending',
  );
  const deniedConfirmations = confirmations.filter(
    (confirmation) =>
      effectiveConfirmationStatus(confirmation) === 'denied' &&
      !isConfirmationExpired(confirmation),
  );
  const displayedConfirmations = [
    ...pendingConfirmations,
    ...deniedConfirmations,
  ];
  const pendingCount = pendingConfirmations.length;
  const bypassOn = canBypassConfirmations && bypassConfirmations;
  const labelNotes = [
    ...(pendingCount > 0 ? [`${pendingCount} pending`] : []),
    ...(bypassOn ? ['bypass on'] : []),
  ];
  const openLabel = labelNotes.length
    ? `Open confirmations (${labelNotes.join(', ')})`
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
      {canBypassConfirmations ? (
        <>
          <Box sx={{ flexShrink: 0, px: 1.5, py: 1 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={bypassConfirmations}
                  color="warning"
                  onChange={(event) =>
                    onBypassConfirmationsChange?.(event.target.checked)
                  }
                  size="small"
                />
              }
              label={
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  Bypass confirmations
                </Typography>
              }
              sx={{ ml: 0, mr: 0 }}
            />
            <Typography
              color="text.secondary"
              sx={{ display: 'block', mt: 0.25 }}
              variant="caption"
            >
              Messages you send run their actions without asking here first.
              Every bypassed action is audit-logged.
            </Typography>
          </Box>
          <Divider />
        </>
      ) : null}
      <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', p: 1.5 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <ConstellationSpinner size={22} />
          </Box>
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : (
          <>
            <Typography
              component="h2"
              sx={{
                borderBottom: 1,
                borderColor: 'divider',
                fontWeight: 700,
                mb: 1,
                pb: 0.5,
              }}
              variant="subtitle2"
            >
              Pending
            </Typography>
            {pendingCount === 0 ? (
              <Typography color="text.secondary" sx={{ mb: 2 }} variant="body2">
                No confirmations pending.
              </Typography>
            ) : null}
            {displayedConfirmations.map((confirmation, index) => {
              const status = effectiveConfirmationStatus(confirmation);
              const canReverseDenial =
                status === 'denied' && !isConfirmationExpired(confirmation);
              return (
                <Fragment key={confirmation.confirmation_id}>
                  {index === pendingCount && deniedConfirmations.length > 0 ? (
                    <Typography
                      component="h2"
                      sx={{
                        borderBottom: 1,
                        borderColor: 'divider',
                        fontWeight: 700,
                        mb: 1.5,
                        pb: 0.5,
                      }}
                      variant="subtitle2"
                    >
                      Previously denied
                    </Typography>
                  ) : null}
                  <Box
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
                      sx={{
                        textTransform: 'capitalize',
                        wordBreak: 'break-word',
                      }}
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
                        <Box
                          sx={{ borderTop: 1, borderColor: 'divider', p: 1 }}
                        >
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
                            ),
                          )}
                        </Box>
                      </Box>
                    )}
                    {status === 'pending' ? (
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
                    ) : null}
                    {canReverseDenial ? (
                      <Box sx={{ mt: 1 }}>
                        <Button
                          color="error"
                          disabled={decidingId === confirmation.confirmation_id}
                          onClick={() => onDecision(confirmation, 'approved')}
                          size="small"
                          variant="outlined"
                        >
                          Accept
                        </Button>
                      </Box>
                    ) : null}
                  </Box>
                </Fragment>
              );
            })}
          </>
        )}
      </Box>
    </Box>
  );

  // Collapsed, the panel is a bar down the right edge at every width, so it
  // folds away like the sessions rail on the left. Beside the chat it expands
  // in place; below `lg` there is no room for that, so it opens as a drawer.
  const rail = (
    <Box
      component="aside"
      sx={{
        borderLeft: 1,
        borderColor: 'divider',
        flexShrink: 0,
        height: '100%',
        width: SIDE_PANEL_RAIL_WIDTH,
      }}
    >
      {/* Same header row as the sessions rail opposite. */}
      <Box
        sx={{
          alignItems: 'center',
          borderBottom: 1,
          borderColor: 'divider',
          display: 'flex',
          justifyContent: 'center',
          minHeight: 40,
        }}
      >
        <Tooltip
          title={bypassOn ? 'Confirmations — bypass on' : 'Confirmations'}
          placement="left"
        >
          <IconButton aria-label={openLabel} onClick={onToggle} size="small">
            <Badge
              badgeContent={pendingCount}
              color="error"
              invisible={pendingCount === 0}
              max={99}
            >
              {/* Amber while confirmations are bypassed, so the setting
                  stays visible with the panel folded away. */}
              <GppMaybe
                fontSize="small"
                sx={{ color: bypassOn ? 'warning.main' : 'inherit' }}
              />
            </Badge>
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );

  if (narrow) {
    return (
      <>
        {rail}
        <Drawer anchor="right" open={open} onClose={onToggle}>
          {content}
        </Drawer>
      </>
    );
  }

  if (!open) return rail;

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

export default memo(ChatConfirmationsPanel);
