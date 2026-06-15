import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  Divider,
  IconButton,
  Grid,
  Typography,
} from '@mui/material';
import Info from '@mui/icons-material/Info';
import Error from '@mui/icons-material/Error';
import { useLazyCypherQuery } from 'src/hooks/useCypherQuery';
import CypherDetails from 'src/components/reports/CypherDetails';
import { ProgressPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';
import type { PanelThreshold } from 'src/config.context';
import { resolveThresholdColor } from 'src/components/reports/thresholds';

const fillCardSx = {
  height: '100%',
  display: 'flex',
  flexDirection: 'column' as const,
};

const fillBodySx = {
  flex: 1,
  minHeight: 0,
  justifyContent: 'center',
};

// Vertical space the numerator/denominator label consumes inside the body.
const TEXT_RESERVE = 56;
// Inner padding around the wheel + label.
const BODY_PADDING = 16;
const MIN_WHEEL = 40;
const MAX_WHEEL = 100;

interface ProgressSettings {
  show_label?: boolean;
}

export function calculateProgressPercent(
  numerator: number,
  denominator: number,
): number {
  if (
    !Number.isFinite(numerator) ||
    !Number.isFinite(denominator) ||
    denominator <= 0
  ) {
    return 0;
  }
  return Math.floor((numerator / denominator) * 100);
}

interface CypherProgressProps {
  cypher?: string;
  params?: Record<string, unknown>;
  caption?: string;
  threshold?: number;
  thresholds?: PanelThreshold[];
  progressSettings?: ProgressSettings;
  details?: Record<string, unknown>;
  needInputs?: string[];
  reportQueryToken?: string;
  refreshKey?: number;
  onTokenExpired?: () => void;
}

export default function CypherProgress({
  cypher,
  params,
  caption,
  threshold,
  thresholds,
  progressSettings,
  details,
  needInputs,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
}: CypherProgressProps) {
  const [open, setOpen] = useState(false);
  const handleClickOpen = () => {
    setOpen(true);
  };

  const observerRef = useRef<ResizeObserver | null>(null);
  const [bodySize, setBodySize] = useState({ w: 0, h: 0 });

  // Callback ref so the observer is re-attached when the body Box swaps
  // between loading / loaded states. Picks up the actual rendered size of
  // the body region so the progress wheel can shrink in small cells.
  const bodyRef = useCallback((node: HTMLDivElement | null) => {
    if (typeof ResizeObserver === 'undefined') return;
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setBodySize({ w: entry.contentRect.width, h: entry.contentRect.height });
    });
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  useEffect(() => {
    return () => {
      observerRef.current?.disconnect();
      observerRef.current = null;
    };
  }, []);

  const [
    runQuery,
    { loading, error, records, first, warnings, queryErrors, tokenExpired },
  ] = useLazyCypherQuery(cypher, reportQueryToken);

  const runQueryRef = useRef(runQuery);
  runQueryRef.current = runQuery;
  const needInputsRef = useRef(needInputs);
  needInputsRef.current = needInputs;

  useEffect(() => {
    if (
      needInputsRef.current === undefined ||
      needInputsRef.current.length === 0
    ) {
      runQueryRef.current(params, { force: (refreshKey ?? 0) > 0 });
    }
  }, [cypher, params, refreshKey]);

  useEffect(() => {
    if (tokenExpired) {
      onTokenExpired?.();
    }
  }, [tokenExpired, onTokenExpired]);

  if (cypher === undefined) {
    return (
      <Card sx={fillCardSx}>
        {caption && (
          <>
            <Grid
              container
              spacing={0}
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
        )}
        <Grid
          container
          spacing={0}
          sx={[fillBodySx, { flexDirection: 'column', alignItems: 'center' }]}
        >
          <CardContent>
            <Error />
            <Typography variant="body2">Missing cypher query</Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (needInputs !== undefined && needInputs.length > 0) {
    return (
      <Card sx={fillCardSx}>
        {caption && (
          <>
            <Grid
              container
              spacing={0}
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
        )}
        <Grid
          container
          spacing={0}
          sx={[fillBodySx, { flexDirection: 'column', alignItems: 'center' }]}
        >
          <CardContent>
            <Typography variant="h4" align="center">
              N/A
            </Typography>
            <Typography variant="body2" align="center">
              (Set {needInputs.join(', ')})
            </Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (error) {
    console.log(error);
    return (
      <Typography variant="body2">
        Failed to load requested data, please reload.
      </Typography>
    );
  }

  if (queryErrors.length > 0) {
    return (
      <Card sx={fillCardSx}>
        {caption && (
          <>
            <Grid
              container
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
        )}
        <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        <Grid
          container
          spacing={0}
          sx={[fillBodySx, { flexDirection: 'column', alignItems: 'center' }]}
        >
          <CardContent>
            <Typography variant="h4" align="center">
              N/A
            </Typography>
            <Typography variant="body2" align="center">
              Query failed
            </Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (loading || records === undefined) {
    return <ProgressPanelSkeleton caption={caption} />;
  }

  if (first === undefined) {
    return (
      <Card sx={fillCardSx}>
        {caption && (
          <>
            <Grid
              container
              spacing={0}
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
        )}
        <Grid
          container
          spacing={0}
          sx={[fillBodySx, { flexDirection: 'column', alignItems: 'center' }]}
        >
          <CardContent>
            <Typography variant="h4">N/A</Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  const numerator = first['numerator'] as number;
  const denominator = first['denominator'] as number;
  const percent = calculateProgressPercent(numerator, denominator);

  type CircularProgressColor =
    | 'inherit'
    | 'primary'
    | 'secondary'
    | 'success'
    | 'error'
    | 'info'
    | 'warning';
  // Use the multi-threshold list when configured; otherwise fall back to
  // the legacy single-threshold semantics (below threshold = red,
  // 100% = green, otherwise primary; with no threshold the cutoff is 70%).
  const hexColor = resolveThresholdColor(percent, thresholds);
  let circleColor: CircularProgressColor | undefined;
  let textColor: string | undefined;
  if (hexColor === undefined) {
    circleColor = 'primary';
    textColor = 'textPrimary';
    if (threshold === undefined) {
      if (percent < 70) {
        circleColor = 'error';
        textColor = 'error';
      } else if (percent === 100) {
        circleColor = 'success';
        textColor = 'success.main';
      }
    } else if (percent < threshold) {
      circleColor = 'error';
      textColor = 'error';
    } else if (percent === 100) {
      circleColor = 'success';
      textColor = 'success.main';
    }
  } else {
    // Custom hex from a threshold rule. ``CircularProgress`` expects a
    // palette token; pass ``inherit`` and let the parent ``Box``'s
    // ``color`` style propagate via ``currentColor``.
    circleColor = 'inherit';
    textColor = hexColor;
  }

  return (
    <>
      <Card
        sx={{
          ...fillCardSx,
          position: 'relative',
          '&:hover .panel-info-btn': { opacity: 1 },
        }}
      >
        <IconButton
          className="panel-info-btn"
          size="small"
          onClick={handleClickOpen}
          sx={{
            position: 'absolute',
            top: 8,
            right: 8,
            opacity: 0,
            transition: 'opacity 0.2s',
          }}
        >
          <Info fontSize="small" />
        </IconButton>
        {caption && (
          <>
            <Grid
              container
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
        )}
        <QueryValidationBadge errors={queryErrors} warnings={warnings} />

        <Box
          ref={bodyRef}
          sx={{
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 1,
            p: 1,
            overflow: 'hidden',
          }}
        >
          {progressSettings?.show_label !== false && (
            <Typography
              component="div"
              variant="h5"
              sx={{ textAlign: 'center', flexShrink: 0 }}
            >
              <Box component="span" color={textColor} sx={{ fontWeight: 500 }}>
                {numerator}
              </Box>
              {' / '}
              {denominator}
            </Typography>
          )}
          <Box
            sx={{
              position: 'relative',
              display: 'inline-flex',
              flexShrink: 1,
              ...(hexColor ? { color: hexColor } : {}),
            }}
          >
            <CircularProgress
              variant="determinate"
              value={percent}
              color={circleColor}
              size={(() => {
                const showLabel = progressSettings?.show_label !== false;
                const wAvail = bodySize.w - BODY_PADDING;
                const hAvail =
                  bodySize.h - BODY_PADDING - (showLabel ? TEXT_RESERVE : 0);
                const fit = Math.min(wAvail, hAvail);
                // When the label is shown the wheel is a secondary element
                // and stays at most 100 px to avoid dwarfing the text. When
                // the label is hidden the wheel IS the panel, so let it
                // fill the available space.
                const cap = showLabel ? MAX_WHEEL : Number.POSITIVE_INFINITY;
                return Math.max(MIN_WHEEL, Math.min(cap, fit));
              })()}
            />
            <Box
              sx={{
                top: 0,
                left: 0,
                bottom: 0,
                right: 0,
                position: 'absolute',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Typography
                variant="caption"
                component="div"
                color="text.secondary"
              >
                {percent}%
              </Typography>
            </Box>
          </Box>
        </Box>
      </Card>
      <CypherDetails details={details} open={open} setOpen={setOpen} />
    </>
  );
}
