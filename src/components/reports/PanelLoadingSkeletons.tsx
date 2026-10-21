import {
  Box,
  Card,
  CardContent,
  CardHeader,
  Divider,
  Paper,
  Skeleton,
  useTheme,
} from '@mui/material';

interface CaptionProps {
  caption?: string;
}

function HeaderSkeleton({ caption }: CaptionProps) {
  if (!caption) return null;
  return (
    <CardHeader
      title={caption}
      slotProps={{ title: { component: 'h3' } }}
      sx={{ textAlign: 'center' }}
    />
  );
}

/** SVG graph content shared between standalone and inline modes. */
function GraphSkeletonSvg({
  nodeColor,
  edgeColor,
}: {
  nodeColor: string;
  edgeColor: string;
}) {
  return (
    <svg
      width="100%"
      height="100%"
      viewBox="0 0 200 160"
      preserveAspectRatio="xMidYMid meet"
    >
      <line
        x1="38"
        y1="42"
        x2="98"
        y2="20"
        stroke={edgeColor}
        strokeWidth="2"
      />
      <line
        x1="98"
        y1="20"
        x2="158"
        y2="38"
        stroke={edgeColor}
        strokeWidth="2"
      />
      <line
        x1="38"
        y1="42"
        x2="65"
        y2="105"
        stroke={edgeColor}
        strokeWidth="2"
      />
      <line
        x1="158"
        y1="38"
        x2="138"
        y2="112"
        stroke={edgeColor}
        strokeWidth="2"
      />
      <line
        x1="65"
        y1="105"
        x2="138"
        y2="112"
        stroke={edgeColor}
        strokeWidth="2"
      />
      <line
        x1="65"
        y1="105"
        x2="30"
        y2="138"
        stroke={edgeColor}
        strokeWidth="2"
      />
      <circle cx="38" cy="42" r="11" fill={nodeColor} />
      <circle cx="98" cy="20" r="8" fill={nodeColor} />
      <circle cx="158" cy="38" r="13" fill={nodeColor} />
      <circle cx="65" cy="105" r="10" fill={nodeColor} />
      <circle cx="138" cy="112" r="9" fill={nodeColor} />
      <circle cx="30" cy="138" r="7" fill={nodeColor} />
    </svg>
  );
}

export function CountPanelSkeleton({
  caption,
  inline = false,
}: CaptionProps & { inline?: boolean }) {
  if (inline) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100%',
        }}
      >
        <Skeleton animation={false} variant="text" width="40%" height={40} />
      </Box>
    );
  }
  return (
    <Card data-testid="count-panel-loading-skeleton" sx={{ minHeight: 150 }}>
      {caption && <HeaderSkeleton caption={caption} />}
      <CardContent sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
        <Skeleton variant="text" width={96} height={56} />
      </CardContent>
    </Card>
  );
}

export function ProgressPanelSkeleton({
  caption,
  inline = false,
}: CaptionProps & { inline?: boolean }) {
  if (inline) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100%',
          gap: 0.75,
        }}
      >
        <Skeleton animation={false} variant="text" width="50%" height={22} />
        <Skeleton animation={false} variant="circular" width={56} height={56} />
      </Box>
    );
  }
  return (
    <Card
      data-testid="progress-panel-loading-skeleton"
      sx={{
        height: '100%',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {caption && <HeaderSkeleton caption={caption} />}
      <CardContent
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 1,
          p: 1,
          '&:last-child': { pb: 1 },
        }}
      >
        <Skeleton
          variant="text"
          width={150}
          height={32}
          sx={{ maxWidth: '70%', flexShrink: 0 }}
        />
        <Skeleton
          variant="circular"
          width={64}
          height={64}
          sx={{ flexShrink: 1 }}
        />
      </CardContent>
    </Card>
  );
}

export function ChartPanelSkeleton({
  caption,
  variant,
  inline = false,
}: CaptionProps & { variant: 'bar' | 'pie'; inline?: boolean }) {
  if (inline) {
    if (variant === 'pie') {
      return (
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            height: '100%',
            gap: 1.5,
            px: 0.5,
          }}
        >
          <Skeleton
            animation={false}
            variant="circular"
            sx={{
              width: '55%',
              height: 'auto',
              aspectRatio: '1',
              maxWidth: 150,
              flexShrink: 0,
            }}
          />
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              gap: 0.75,
              justifyContent: 'center',
            }}
          >
            <Skeleton animation={false} variant="text" width={72} height={14} />
            <Skeleton animation={false} variant="text" width={58} height={14} />
            <Skeleton animation={false} variant="text" width={68} height={14} />
            <Skeleton animation={false} variant="text" width={44} height={14} />
          </Box>
        </Box>
      );
    }
    return (
      <Box
        sx={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 0.75,
          height: '100%',
          px: 0.5,
          pb: 0.5,
        }}
      >
        {[50, 75, 55, 90, 65, 40, 80].map((h, index) => (
          <Skeleton
            animation={false}
            key={index}
            variant="rectangular"
            sx={{ flex: 1, height: `${h}%`, borderRadius: 0.5 }}
          />
        ))}
      </Box>
    );
  }
  return (
    <Card
      data-testid={`${variant}-panel-loading-skeleton`}
      sx={{ minHeight: 410 }}
    >
      {caption && <HeaderSkeleton caption={caption} />}
      <Box
        sx={{
          height: 350,
          p: 2,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {variant === 'pie' ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <Skeleton variant="circular" width={220} height={220} />
            <Box sx={{ display: 'grid', gap: 1 }}>
              <Skeleton variant="text" width={120} height={22} />
              <Skeleton variant="text" width={100} height={22} />
              <Skeleton variant="text" width={140} height={22} />
            </Box>
          </Box>
        ) : (
          <Box
            sx={{
              width: '100%',
              height: '100%',
              display: 'flex',
              alignItems: 'flex-end',
              gap: 1.5,
              px: 4,
              pb: 4,
            }}
          >
            {[45, 72, 54, 88, 64, 38, 78].map((height, index) => (
              <Skeleton
                key={index}
                variant="rectangular"
                width="12%"
                height={`${height}%`}
                sx={{ borderRadius: 1 }}
              />
            ))}
          </Box>
        )}
      </Box>
    </Card>
  );
}

export function GraphPanelSkeleton({
  caption,
  fillHeight = false,
  inline = false,
}: CaptionProps & { fillHeight?: boolean; inline?: boolean }) {
  const theme = useTheme();
  const nodeColor =
    theme.palette.mode === 'dark'
      ? 'rgba(255,255,255,0.13)'
      : 'rgba(0,0,0,0.11)';
  const edgeColor =
    theme.palette.mode === 'dark'
      ? 'rgba(255,255,255,0.07)'
      : 'rgba(0,0,0,0.07)';

  if (inline) {
    return (
      <Box sx={{ height: '100%', px: 0.5, pb: 0.5 }}>
        <GraphSkeletonSvg nodeColor={nodeColor} edgeColor={edgeColor} />
      </Box>
    );
  }
  return (
    <Card
      data-testid="graph-panel-loading-skeleton"
      sx={
        fillHeight
          ? { height: '100%', display: 'flex', flexDirection: 'column' }
          : { minHeight: 500 }
      }
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          minHeight: 48,
          px: 2,
          gap: 2,
        }}
      >
        <Skeleton variant="text" width={64} height={28} />
        <Skeleton variant="text" width={58} height={28} />
        <Skeleton variant="text" width={44} height={28} />
        {caption && (
          <Box sx={{ ml: 'auto' }}>
            <Skeleton variant="text" width={160} height={28} />
          </Box>
        )}
      </Box>
      <Divider />
      <Box
        sx={{
          flex: fillHeight ? 1 : 'none',
          height: fillHeight ? 'auto' : 450,
          minHeight: fillHeight ? 0 : undefined,
          display: 'flex',
        }}
      >
        <Box sx={{ flex: 1, p: 2 }}>
          <GraphSkeletonSvg nodeColor={nodeColor} edgeColor={edgeColor} />
        </Box>
        <Divider orientation="vertical" flexItem />
        <Box
          sx={{
            width: 280,
            p: 2,
            display: { xs: 'none', md: 'grid' },
            alignContent: 'start',
            gap: 1,
          }}
        >
          <Skeleton variant="text" width={120} height={28} />
          <Skeleton variant="text" width="90%" height={22} />
          <Skeleton variant="text" width="78%" height={22} />
          <Skeleton variant="text" width="84%" height={22} />
        </Box>
      </Box>
    </Card>
  );
}

/**
 * Skeleton for table panels. In standalone mode mirrors the CypherTable chrome
 * (toolbar, column headers, rows, pagination). In inline mode fills available
 * height with evenly distributed rows.
 */
export function TablePanelSkeleton({
  height,
  inline = false,
}: {
  height?: string;
  inline?: boolean;
}) {
  if (inline) {
    return (
      <Box
        sx={{
          height: '100%',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          px: 0.5,
        }}
      >
        <Skeleton
          animation={false}
          variant="rectangular"
          width="100%"
          height={26}
          sx={{ borderRadius: 0.5, flexShrink: 0, mb: 0.5 }}
        />
        <Box
          sx={{
            flex: 1,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-around',
          }}
        >
          {[0, 1, 2, 3, 4, 5, 6].map((index) => (
            <Box key={index} sx={{ display: 'flex', gap: 0.5 }}>
              <Skeleton
                animation={false}
                variant="rectangular"
                sx={{ flex: 2 }}
                height={20}
              />
              <Skeleton
                animation={false}
                variant="rectangular"
                sx={{ flex: 3 }}
                height={20}
              />
              <Skeleton
                animation={false}
                variant="rectangular"
                sx={{ flex: 1 }}
                height={20}
              />
            </Box>
          ))}
        </Box>
      </Box>
    );
  }
  const bodyHeight = height ?? '475px';
  return (
    <Paper
      data-testid="cypher-table-loading-skeleton"
      variant="outlined"
      sx={{ overflow: 'hidden' }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          minHeight: 44,
          px: 1,
        }}
      >
        <Skeleton variant="text" width={180} height={22} />
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <Skeleton variant="circular" width={24} height={24} />
          <Skeleton variant="circular" width={24} height={24} />
          <Skeleton variant="circular" width={24} height={24} />
          <Skeleton variant="circular" width={24} height={24} />
        </Box>
      </Box>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: '1.2fr 1fr 1fr',
          alignItems: 'center',
          minHeight: 40,
          gap: 2,
          px: 1.5,
          borderTop: 1,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Skeleton variant="text" height={20} />
        <Skeleton variant="text" height={20} />
        <Skeleton variant="text" height={20} />
      </Box>
      <Box sx={{ height: bodyHeight, px: 1.5, py: 1 }}>
        {Array.from({ length: 8 }).map((_, index) => (
          <Box
            key={index}
            sx={{
              display: 'grid',
              gridTemplateColumns: '1.2fr 1fr 1fr',
              gap: 2,
              py: 0.75,
            }}
          >
            <Skeleton variant="text" height={20} />
            <Skeleton variant="text" height={20} />
            <Skeleton variant="text" height={20} />
          </Box>
        ))}
      </Box>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          minHeight: 40,
          gap: 2,
          px: 1.5,
          borderTop: 1,
          borderColor: 'divider',
        }}
      >
        <Skeleton variant="text" width={90} height={20} />
        <Skeleton variant="text" width={120} height={20} />
      </Box>
    </Paper>
  );
}

export function VerticalTableSkeleton({
  inline = false,
}: { inline?: boolean } = {}) {
  if (inline) {
    return (
      <Box
        sx={{
          height: '100%',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          px: 0.5,
        }}
      >
        <Box
          sx={{
            flex: 1,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-around',
          }}
        >
          {[0, 1, 2, 3, 4, 5, 6].map((index) => (
            <Box
              key={index}
              sx={{ display: 'grid', gridTemplateColumns: '40% 1fr', gap: 1 }}
            >
              <Skeleton animation={false} variant="rectangular" height={20} />
              <Skeleton animation={false} variant="rectangular" height={20} />
            </Box>
          ))}
        </Box>
      </Box>
    );
  }
  return (
    <Box data-testid="vertical-table-loading-skeleton">
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 1,
        }}
      >
        <Skeleton variant="text" width={180} height={32} />
        <Skeleton variant="circular" width={32} height={32} />
      </Box>
      <Divider />
      <Paper sx={{ p: 2, mt: 1 }}>
        {Array.from({ length: 7 }).map((_, index) => (
          <Box
            key={index}
            sx={{
              display: 'grid',
              gridTemplateColumns: '180px 1fr',
              gap: 2,
              py: 1,
            }}
          >
            <Skeleton variant="text" height={22} />
            <Skeleton variant="text" height={22} />
          </Box>
        ))}
      </Paper>
    </Box>
  );
}

export function MarkdownPanelSkeleton({
  caption,
  inline = false,
}: CaptionProps & { inline?: boolean }) {
  if (inline) {
    return (
      <Box
        sx={{
          height: '100%',
          overflow: 'hidden',
          px: 0.5,
          display: 'flex',
          flexDirection: 'column',
          gap: 0,
        }}
      >
        <Skeleton
          animation={false}
          variant="text"
          width="55%"
          height={20}
          sx={{ mb: 0.5 }}
        />
        <Skeleton animation={false} variant="text" width="97%" height={13} />
        <Skeleton animation={false} variant="text" width="92%" height={13} />
        <Skeleton animation={false} variant="text" width="95%" height={13} />
        <Skeleton
          animation={false}
          variant="text"
          width="48%"
          height={13}
          sx={{ mb: 1 }}
        />
        <Skeleton animation={false} variant="text" width="94%" height={13} />
        <Skeleton animation={false} variant="text" width="89%" height={13} />
        <Skeleton
          animation={false}
          variant="text"
          width="60%"
          height={13}
          sx={{ mb: 1 }}
        />
        <Skeleton animation={false} variant="text" width="96%" height={13} />
        <Skeleton animation={false} variant="text" width="78%" height={13} />
      </Box>
    );
  }
  return (
    <Card sx={{ minHeight: 200 }}>
      {caption && <HeaderSkeleton caption={caption} />}
      <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        <Skeleton variant="text" width="55%" height={24} sx={{ mb: 0.5 }} />
        <Skeleton variant="text" width="97%" height={16} />
        <Skeleton variant="text" width="92%" height={16} />
        <Skeleton variant="text" width="95%" height={16} />
        <Skeleton variant="text" width="48%" height={16} sx={{ mb: 1.5 }} />
        <Skeleton variant="text" width="94%" height={16} />
        <Skeleton variant="text" width="89%" height={16} />
        <Skeleton variant="text" width="60%" height={16} />
      </CardContent>
    </Card>
  );
}

/** Thin dispatcher: renders the appropriate skeleton in inline/edit-card mode. */
export function EditPanelSkeleton({ type }: { type: string }) {
  switch (type) {
    case 'count':
      return <CountPanelSkeleton inline />;
    case 'progress':
      return <ProgressPanelSkeleton inline />;
    case 'bar':
      return <ChartPanelSkeleton variant="bar" inline />;
    case 'pie':
      return <ChartPanelSkeleton variant="pie" inline />;
    case 'graph':
      return <GraphPanelSkeleton inline />;
    case 'table':
      return <TablePanelSkeleton inline />;
    case 'vertical-table':
      return <VerticalTableSkeleton inline />;
    case 'markdown':
      return <MarkdownPanelSkeleton inline />;
    default:
      return (
        <Box sx={{ height: '100%', px: 0.5, pb: 0.5 }}>
          <Skeleton
            animation={false}
            variant="rectangular"
            width="100%"
            height="100%"
            sx={{ borderRadius: 0.5 }}
          />
        </Box>
      );
  }
}
