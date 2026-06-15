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
import { PieChart } from '@mui/x-charts/PieChart';
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

export function buildPieData(
  records: QueryRecord[],
): { id: string; value: number; label: string }[] {
  const pieData: { id: string; value: number; label: string }[] = [];
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
    const value = Number(mungedData['value'] ?? 0);
    pieData.push({
      id: String(mungedData['id'] ?? i),
      value: Number.isFinite(value) ? value : 0,
      label: String(mungedData['id'] ?? i),
    });
  }
  return pieData;
}

interface CypherPieProps {
  cypher?: string;
  params?: Record<string, unknown>;
  caption?: string;
  pieSettings?: { legend?: string };
  details?: Record<string, unknown>;
  needInputs?: string[];
  refreshKey?: number;
  onTokenExpired?: () => void;
  reportQueryToken?: string;
}

export default function CypherPie({
  cypher,
  params,
  caption,
  pieSettings,
  details,
  needInputs,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
}: CypherPieProps) {
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
    return <ChartPanelSkeleton caption={caption} variant="pie" />;
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

  const pieData = buildPieData(records);

  const hasLegend = !!pieSettings?.legend;

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
          <PieChart
            series={[
              {
                data: pieData,
                // Donut style — looks cleaner with labels
                innerRadius: '35%',
                outerRadius: '80%',
                paddingAngle: 2,
                cornerRadius: 4,
                // Show arc labels only when no legend is configured
                arcLabel: hasLegend ? undefined : 'label',
                arcLabelMinAngle: 20,
                arcLabelRadius: '60%',
                highlightScope: { fade: 'global', highlight: 'item' },
                // faded.innerRadius only accepts number, not string
                faded: {
                  additionalRadius: -4,
                  color: theme.palette.action.disabled,
                },
                valueFormatter: (item) => String(item.value),
              },
            ]}
            colors={chartColorsFor(theme.palette.mode)}
            hideLegend={!hasLegend}
            margin={
              pieSettings?.legend === 'column'
                ? { top: 16, right: 160, bottom: 16, left: 16 }
                : pieSettings?.legend === 'row'
                  ? { top: 16, right: 16, bottom: 80, left: 16 }
                  : { top: 24, right: 24, bottom: 24, left: 24 }
            }
            slotProps={{
              ...(hasLegend && {
                legend: {
                  position:
                    pieSettings?.legend === 'column'
                      ? { vertical: 'middle', horizontal: 'end' }
                      : { vertical: 'bottom', horizontal: 'center' },
                  direction:
                    pieSettings?.legend === 'column'
                      ? 'vertical'
                      : 'horizontal',
                },
              }),
              pieArcLabel: {
                style: {
                  fontFamily: theme.typography.fontFamily ?? undefined,
                  fontSize: 12,
                  fontWeight: 500,
                },
              },
            }}
          />
        </Box>
      </Card>
      <CypherDetails details={details} open={open} setOpen={setOpen} />
    </>
  );
}
