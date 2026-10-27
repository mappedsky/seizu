import { Fragment, type ReactNode } from 'react';
import { Box, ButtonBase, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import { temporalStatusColor, temporalStatusLabel } from 'src/temporalStatus';
import { ACTIVE_RUN_STATUSES } from 'src/workflowRunStages';

export interface StageLane {
  key: string;
  title: string;
  /** Short qualifier beside the title, e.g. "2 in parallel". */
  meta?: string;
  children: ReactNode;
}

/**
 * A workflow's stages as columns read left to right, with each stage's
 * parallel activities stacked inside it. Stacks vertically on narrow screens.
 */
export default function WorkflowStageLanes({
  lanes,
  label,
}: {
  lanes: StageLane[];
  label: string;
}) {
  return (
    <Box
      component="ol"
      aria-label={label}
      sx={{
        display: 'flex',
        flexDirection: { xs: 'column', md: 'row' },
        alignItems: { xs: 'stretch', md: 'flex-start' },
        gap: 0,
        listStyle: 'none',
        m: 0,
        p: 0,
        overflowX: { md: 'auto' },
        pb: { md: 0.5 },
      }}
    >
      {lanes.map((lane, index) => (
        <Fragment key={lane.key}>
          {index > 0 && (
            <Box
              aria-hidden="true"
              sx={{
                alignSelf: { xs: 'center', md: 'auto' },
                color: 'text.disabled',
                display: 'flex',
                flexShrink: 0,
                px: { md: 0.75 },
                py: { xs: 0.5, md: 0 },
                // Sits level with the stage titles.
                mt: { md: 1.25 },
              }}
            >
              <ArrowForwardIcon
                fontSize="small"
                sx={{ display: { xs: 'none', md: 'block' } }}
              />
              <ArrowDownwardIcon
                fontSize="small"
                sx={{ display: { xs: 'block', md: 'none' } }}
              />
            </Box>
          )}
          <Box
            component="li"
            sx={{
              flex: { md: '1 1 0' },
              minWidth: { md: 220 },
              maxWidth: { md: 380 },
            }}
          >
            <Box
              sx={{
                alignItems: 'baseline',
                display: 'flex',
                gap: 1,
                mb: 1,
                minHeight: 28,
                pt: 0.75,
              }}
            >
              <Typography component="h3" variant="h5">
                {lane.title}
              </Typography>
              {lane.meta && (
                <Typography variant="caption" color="text.secondary">
                  {lane.meta}
                </Typography>
              )}
            </Box>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {lane.children}
            </Box>
          </Box>
        </Fragment>
      ))}
    </Box>
  );
}

/** One selectable activity tile inside a lane. */
export function LaneTile({
  selected,
  onClick,
  children,
  tone,
  invalid = false,
  id,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
  /** Status color tinting the tile, for run views. */
  tone?: string;
  /** Marks the tile in the error hue whether or not it is selected. */
  invalid?: boolean;
  id?: string;
}) {
  const theme = useTheme();
  const accent = invalid
    ? theme.palette.error.main
    : (tone ?? theme.palette.primary.main);
  return (
    <ButtonBase
      id={id}
      aria-pressed={selected}
      onClick={onClick}
      sx={{
        alignItems: 'stretch',
        bgcolor: selected ? alpha(accent, 0.1) : 'background.paper',
        border: 1,
        borderColor: selected || invalid ? accent : 'divider',
        borderRadius: 1,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
        px: 1.5,
        py: 1.25,
        textAlign: 'left',
        transition: theme.transitions.create(
          ['border-color', 'background-color'],
          { duration: theme.transitions.duration.shorter },
        ),
        width: '100%',
        '&:hover': {
          borderColor: selected ? accent : alpha(accent, 0.5),
        },
        '&.Mui-focusVisible': {
          outline: `2px solid ${theme.palette.primary.main}`,
          outlineOffset: 2,
        },
      }}
    >
      {children}
    </ButtonBase>
  );
}

export function useStatusTone(status: string | null): string {
  const theme = useTheme();
  const color = temporalStatusColor(status);
  return color === 'default'
    ? theme.palette.text.secondary
    : theme.palette[color].main;
}

/** A status as a colored dot and label; the dot pulses while work is live. */
export function StatusMark({
  status,
  size = 'small',
}: {
  status: string | null;
  size?: 'small' | 'medium';
}) {
  const tone = useStatusTone(status);
  const live = status !== null && ACTIVE_RUN_STATUSES.has(status);
  const dot = size === 'medium' ? 10 : 8;
  return (
    <Box
      component="span"
      sx={{
        alignItems: 'center',
        color: tone,
        display: 'inline-flex',
        gap: 0.75,
        whiteSpace: 'nowrap',
      }}
    >
      <Box
        component="span"
        aria-hidden="true"
        sx={{
          bgcolor: tone,
          borderRadius: '50%',
          flexShrink: 0,
          height: dot,
          width: dot,
          ...(live && {
            '@keyframes statusPulse': {
              '0%': { boxShadow: `0 0 0 0 ${alpha(tone, 0.55)}` },
              '100%': { boxShadow: `0 0 0 ${dot}px ${alpha(tone, 0)}` },
            },
            animation:
              'statusPulse 1.6s cubic-bezier(0.16, 1, 0.3, 1) infinite',
            '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
          }),
        }}
      />
      <Typography
        component="span"
        variant={size === 'medium' ? 'body2' : 'caption'}
        sx={{ color: 'inherit', fontWeight: 600 }}
      >
        {temporalStatusLabel(status)}
      </Typography>
    </Box>
  );
}
