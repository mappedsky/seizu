import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  Grid,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import Error from '@mui/icons-material/Error';
import { PieChart } from '@mui/x-charts/PieChart';
import { brand, chartColorsFor } from 'src/theme/brand';
import {
  compareSeverity,
  isSeverityScale,
  severityColor,
  severityOf,
} from 'src/theme/severity';
import { useLazyCypherQuery, QueryRecord } from 'src/hooks/useCypherQuery';
import CypherDetails from 'src/components/reports/LazyCypherDetails';
import { ChartPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';
import PanelInfoButton, {
  panelInfoRevealSx,
} from 'src/components/reports/PanelInfoButton';

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

/**
 * A severity breakdown is ordered most urgent first and drawn in each level's
 * fixed color; any other breakdown is returned as is, for the chart palette.
 */
export function severityPieData(
  data: { id: string; value: number; label: string }[],
  mode: 'light' | 'dark',
): { id: string; value: number; label: string; color?: string }[] {
  if (!isSeverityScale(data.map((item) => item.id))) return data;
  return [...data]
    .sort((a, b) => compareSeverity(a.id, b.id))
    .map((item) => ({
      ...item,
      color: severityColor(severityOf(item.id)!, mode),
    }));
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
  // A side legend takes a fixed strip of the panel; on a phone that strip is
  // most of it, so the legend goes below the chart instead.
  const phone = useMediaQuery(theme.breakpoints.down('sm'), { noSsr: true });
  const legend =
    phone && pieSettings?.legend === 'column' ? 'row' : pieSettings?.legend;
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
          <Grid
            container
            spacing={0}
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
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
          <Grid
            container
            spacing={0}
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
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
          <Grid
            container
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
        )}
        <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        <Grid
          container
          spacing={0}
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="h4" component="p" align="center">
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
          <Grid
            container
            spacing={0}
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
        )}
        <Grid
          container
          spacing={0}
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="h4" component="p">
              N/A
            </Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  const pieData = severityPieData(buildPieData(records), theme.palette.mode);

  const hasLegend = !!legend;
  const severityScale = pieData.some((item) => 'color' in item);

  return (
    <>
      <Card
        sx={{
          ...fillCardSx,
          position: 'relative',
          ...panelInfoRevealSx,
        }}
      >
        <PanelInfoButton onClick={() => setOpen(true)} panelName={caption} />
        {caption && (
          <Grid
            container
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
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
              legend === 'column'
                ? { top: 16, right: 160, bottom: 16, left: 16 }
                : legend === 'row'
                  ? { top: 16, right: 16, bottom: 80, left: 16 }
                  : { top: 24, right: 24, bottom: 24, left: 24 }
            }
            slotProps={{
              ...(hasLegend && {
                legend: {
                  position:
                    legend === 'column'
                      ? { vertical: 'middle', horizontal: 'end' }
                      : { vertical: 'bottom', horizontal: 'center' },
                  direction: legend === 'column' ? 'vertical' : 'horizontal',
                },
              }),
              pieArcLabel: {
                style: {
                  // The dark-mode palette is all light tones, which white
                  // text cannot be read against; Space reads on every one,
                  // and on every severity color in either mode.
                  fill:
                    theme.palette.mode === 'dark' || severityScale
                      ? brand.space
                      : '#fff',
                  fontFamily: theme.typography.fontFamily ?? undefined,
                  fontSize: 12,
                  fontWeight: 600,
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
