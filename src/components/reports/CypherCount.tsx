import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  Divider,
  IconButton,
  Grid,
  Typography,
} from '@mui/material';
import Info from '@mui/icons-material/Info';
import Error from '@mui/icons-material/Error';
import { useLazyCypherQuery } from 'src/hooks/useCypherQuery';
import CypherDetails from 'src/components/reports/CypherDetails';
import { CountPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
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

// Bounds for the responsive number font size (px).
const MIN_FONT_PX = 12;
const MAX_FONT_PX = 120;
// Inner padding inside the body wrapper (sx={{ p: 1 }} → 8 px each side).
const BODY_PADDING_PX = 16;
// Approximate ratio of digit width to font size in MUI's default sans font.
// Used to budget the font size against available width per character.
const DIGIT_WIDTH_RATIO = 0.65;
// Fraction of the available height the rendered text should occupy. Leaves
// a little breathing room above and below.
const HEIGHT_FILL_RATIO = 0.85;

interface CypherCountProps {
  cypher?: string;
  params?: Record<string, unknown>;
  caption?: string;
  threshold?: number;
  thresholds?: PanelThreshold[];
  details?: Record<string, unknown>;
  needInputs?: string[];
  reportQueryToken?: string;
  refreshKey?: number;
  onTokenExpired?: () => void;
}

export default function CypherCount({
  cypher,
  params,
  caption,
  threshold,
  thresholds,
  details,
  needInputs,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
}: CypherCountProps) {
  const [open, setOpen] = useState(false);
  const handleClickOpen = () => {
    setOpen(true);
  };

  const observerRef = useRef<ResizeObserver | null>(null);
  const [bodySize, setBodySize] = useState({ w: 0, h: 0 });

  // Callback ref re-attaches the observer when the body Box swaps between
  // states (loading vs loaded). Used to scale the number's font size to
  // the available area.
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

  // Always call the latest runQuery without re-running when only the token changes.
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
    return <CountPanelSkeleton caption={caption} />;
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

  const total = first['total'] as number;
  // Pick the active color from the multi-threshold list when set, otherwise
  // fall back to the legacy single-threshold logic.
  const thresholdColor = resolveThresholdColor(total, thresholds);
  let legacyColor: string | undefined;
  if (thresholdColor === undefined) {
    if (threshold !== undefined && total > threshold) {
      legacyColor = 'error.main';
    }
  }
  const totalColor = thresholdColor ?? legacyColor;

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
            alignItems: 'center',
            justifyContent: 'center',
            p: 1,
            overflow: 'hidden',
          }}
        >
          <Typography
            component="span"
            sx={{
              ...(totalColor ? { color: totalColor } : {}),
              fontWeight: 500,
              lineHeight: 1,
              fontSize: `${(() => {
                const charCount = Math.max(1, String(total).length);
                const widthBudget = Math.max(0, bodySize.w - BODY_PADDING_PX);
                const heightBudget = Math.max(0, bodySize.h - BODY_PADDING_PX);
                const widthFit = widthBudget / (charCount * DIGIT_WIDTH_RATIO);
                const heightFit = heightBudget * HEIGHT_FILL_RATIO;
                const fit = Math.min(widthFit, heightFit);
                return Math.max(MIN_FONT_PX, Math.min(MAX_FONT_PX, fit));
              })()}px`,
            }}
          >
            {total}
          </Typography>
        </Box>
      </Card>
      <CypherDetails details={details} open={open} setOpen={setOpen} />
    </>
  );
}
