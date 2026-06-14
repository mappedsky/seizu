import { useEffect, useRef, useState } from 'react';
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
import { useTheme } from '@mui/material/styles';
import Info from '@mui/icons-material/Info';
import Error from '@mui/icons-material/Error';
import { BarChart } from '@mui/x-charts/BarChart';
import { chartColorsFor } from 'src/theme/brand';
import { useLazyCypherQuery, QueryRecord } from 'src/hooks/useCypherQuery';
import CypherDetails from 'src/components/reports/CypherDetails';
import { ChartPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';

const fillCardSx = {
  height: '100%',
  display: 'flex',
  flexDirection: 'column' as const,
};

const chartFillSx = {
  flex: 1,
  minHeight: 0,
  display: 'flex',
};

const MAX_AXIS_LABEL_LINES = 2;
const MAX_AXIS_LABEL_CHARS = 18;
const DEFAULT_AXIS_LABEL_LINE_LENGTH = 9;

export function ellipsizeText(value: string, maxChars: number): string {
  if (value.length <= maxChars) {
    return value;
  }
  if (maxChars <= 3) {
    return '.'.repeat(Math.max(0, maxChars));
  }
  return `${value.slice(0, maxChars - 3).trimEnd()}...`;
}

export function wrapAxisLabel(
  value: string,
  lineLength = DEFAULT_AXIS_LABEL_LINE_LENGTH,
  maxLines = MAX_AXIS_LABEL_LINES,
  maxChars = MAX_AXIS_LABEL_CHARS,
): string {
  const label = value.trim();
  if (label.length <= lineLength) {
    return label;
  }

  const words = label.split(/\s+/);
  const lines: string[] = [];
  let current = '';

  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= lineLength) {
      current = next;
      continue;
    }
    if (current) {
      lines.push(current);
      current = word;
    } else {
      lines.push(word);
      current = '';
    }
  }
  if (current) {
    lines.push(current);
  }

  const boundedLines = lines.slice(0, maxLines);
  if (lines.length > maxLines) {
    boundedLines[maxLines - 1] =
      `${boundedLines[maxLines - 1]} ${lines.slice(maxLines).join(' ')}`;
  }

  return boundedLines.map((line) => ellipsizeText(line, maxChars)).join('\n');
}

interface BarSettings {
  legend?: string;
}

export function buildBarDataset(
  records: QueryRecord[],
): Record<string, string | number>[] {
  const mungedRecords: Record<string, string | number>[] = [];
  for (let i = 0; i < records.length; i++) {
    const data = records[i];
    let mungedData: Record<string, unknown>;
    const dataDetails = data['details'] as QueryRecord & {
      properties?: QueryRecord;
    };
    if (dataDetails.properties === undefined) {
      mungedData = dataDetails;
    } else {
      mungedData = dataDetails.properties;
    }
    const flat: Record<string, string | number> = {};
    Object.keys(mungedData).forEach((key) => {
      const val = mungedData[key];
      if (Array.isArray(val)) {
        flat[key] = (val as unknown[]).join(', ');
      } else if (val === null || val === undefined) {
        flat[key] = 0;
      } else if (typeof val === 'string' || typeof val === 'number') {
        flat[key] = val;
      } else {
        flat[key] = String(val);
      }
    });
    mungedRecords.push(flat);
  }
  return mungedRecords;
}

interface CypherBarProps {
  cypher?: string;
  params?: Record<string, unknown>;
  caption?: string;
  barSettings?: BarSettings;
  details?: Record<string, unknown>;
  needInputs?: string[];
  reportQueryToken?: string;
  refreshKey?: number;
  onTokenExpired?: () => void;
}

export default function CypherBar({
  cypher,
  params,
  caption,
  barSettings,
  details,
  needInputs,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
}: CypherBarProps) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);

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
          sx={{ flexDirection: 'column', alignItems: 'center' }}
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
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="body2" align="center">
              (Set {needInputs.join(', ')})
            </Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (error) {
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
          sx={{ flexDirection: 'column', alignItems: 'center' }}
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
    return <ChartPanelSkeleton caption={caption} variant="bar" />;
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
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="h4">N/A</Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  const mungedRecords = buildBarDataset(records);

  const hasLegend = !!barSettings?.legend;
  // Object literal without explicit React.CSSProperties type — ChartsTextStyle is stricter
  const tickLabelStyle = {
    fill: theme.palette.text.secondary,
    fontFamily: theme.typography.fontFamily,
    fontSize: 12,
  };

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
          onClick={() => setOpen(true)}
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

        <Box sx={chartFillSx}>
          <BarChart
            dataset={mungedRecords}
            xAxis={[
              {
                scaleType: 'band',
                dataKey: 'id',
                height: 48,
                disableLine: true,
                disableTicks: true,
                tickLabelInterval: () => true,
                tickLabelMinGap: 2,
                valueFormatter: (value) => wrapAxisLabel(String(value)),
                tickLabelStyle,
              },
            ]}
            yAxis={[
              {
                disableLine: true,
                disableTicks: true,
                tickLabelStyle,
              },
            ]}
            series={[
              {
                dataKey: 'value',
                label: caption ?? 'Value',
              },
            ]}
            borderRadius={6}
            colors={chartColorsFor(theme.palette.mode)}
            grid={{ horizontal: true }}
            hideLegend={!hasLegend}
            margin={
              barSettings?.legend === 'column'
                ? { top: 16, right: 150, bottom: 56, left: 48 }
                : barSettings?.legend === 'row'
                  ? { top: 16, right: 16, bottom: 88, left: 48 }
                  : { top: 16, right: 16, bottom: 56, left: 48 }
            }
            slotProps={
              hasLegend
                ? {
                    legend: {
                      position:
                        barSettings?.legend === 'column'
                          ? { vertical: 'middle', horizontal: 'end' }
                          : { vertical: 'bottom', horizontal: 'center' },
                      direction:
                        barSettings?.legend === 'column'
                          ? 'vertical'
                          : 'horizontal',
                    },
                  }
                : undefined
            }
            sx={{
              '& .MuiChartsGrid-line': {
                stroke: theme.palette.divider,
                strokeDasharray: '4 4',
              },
            }}
          />
        </Box>
      </Card>
      <CypherDetails details={details} open={open} setOpen={setOpen} />
    </>
  );
}
