import { Box, Chip, Typography } from '@mui/material';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import DetailDialog, {
  DetailSection,
  DetailCodeBlock,
} from 'src/components/DetailDialog';
import {
  ScheduledQueryParam,
  ScheduledQueryWatchScan,
  ScheduledQueryAction,
  ScheduledQueryRunError,
} from 'src/hooks/useScheduledQueriesApi';
import { ScheduleSpec, describeSchedule } from 'src/scheduleSpec';

export interface ScheduledQueryViewData {
  name: string;
  version?: number;
  cypher: string;
  params: ScheduledQueryParam[];
  frequency: number | null;
  schedule: ScheduleSpec | null;
  watch_scans: ScheduledQueryWatchScan[];
  enabled: boolean;
  actions: ScheduledQueryAction[];
  last_run_status?: string | null;
  last_run_at?: string | null;
  last_errors?: ScheduledQueryRunError[];
}

interface Props {
  open: boolean;
  onClose: () => void;
  data: ScheduledQueryViewData | null;
}

export default function ScheduledQueryDetailDialog({
  open,
  onClose,
  data,
}: Props) {
  if (!data) return null;

  const triggerLabel =
    data.watch_scans.length > 0
      ? `Watch scans (${data.watch_scans.length})`
      : data.schedule
        ? describeSchedule(data.schedule)
        : data.frequency != null
          ? `Every ${data.frequency} min`
          : 'Not configured';

  return (
    <DetailDialog
      open={open}
      onClose={onClose}
      title={data.name}
      secondary={data.version !== undefined ? `v${data.version}` : undefined}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        <DetailSection title="Status">
          <Chip
            label={data.enabled ? 'Enabled' : 'Disabled'}
            color={data.enabled ? 'success' : 'default'}
            size="small"
          />
        </DetailSection>

        <DetailSection title="Last Run">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <FiberManualRecordIcon
              sx={{
                fontSize: 12,
                color:
                  data.last_run_status === 'success'
                    ? 'success.main'
                    : data.last_run_status === 'failure'
                      ? 'error.main'
                      : 'warning.main',
              }}
            />
            <Typography variant="body2">
              {data.last_run_status === 'success'
                ? 'Success'
                : data.last_run_status === 'failure'
                  ? 'Failed'
                  : 'No runs yet'}
            </Typography>
            {data.last_run_at && (
              <Typography variant="body2" color="text.secondary">
                {new Date(data.last_run_at).toLocaleString()}
              </Typography>
            )}
          </Box>
        </DetailSection>

        {data.last_errors && data.last_errors.length > 0 && (
          <DetailSection title="Recent Errors">
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {data.last_errors.map((err, i) => (
                <Box
                  key={i}
                  sx={{
                    border: '1px solid',
                    borderColor: 'error.light',
                    borderRadius: 1,
                    p: 1.5,
                  }}
                >
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    {new Date(err.timestamp).toLocaleString()}
                  </Typography>
                  <DetailCodeBlock>{err.error}</DetailCodeBlock>
                </Box>
              ))}
            </Box>
          </DetailSection>
        )}

        <DetailSection title="Trigger">
          <Typography variant="body2">{triggerLabel}</Typography>
          {data.watch_scans.length > 0 && (
            <Box sx={{ mt: 0.5 }}>
              {data.watch_scans.map((ws, i) => (
                <Typography
                  key={i}
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: 'block' }}
                >
                  grouptype: {ws.grouptype ?? '*'} &nbsp;·&nbsp; syncedtype:{' '}
                  {ws.syncedtype ?? '*'} &nbsp;·&nbsp; groupid:{' '}
                  {ws.groupid ?? '*'}
                </Typography>
              ))}
            </Box>
          )}
        </DetailSection>

        <DetailSection title="Cypher">
          <DetailCodeBlock>{data.cypher}</DetailCodeBlock>
        </DetailSection>

        {data.params.length > 0 && (
          <DetailSection title="Parameters">
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              {data.params.map((p, i) => (
                <Box key={i} sx={{ display: 'flex', gap: 1 }}>
                  <Typography
                    variant="body2"
                    sx={{
                      fontFamily: 'monospace',
                      fontWeight: 600,
                      minWidth: 120,
                    }}
                  >
                    {p.name}
                  </Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ fontFamily: 'monospace' }}
                  >
                    {Array.isArray(p.value)
                      ? (p.value as unknown[]).join(', ')
                      : String(p.value ?? '')}
                    {Array.isArray(p.value) && (
                      <Typography
                        component="span"
                        variant="caption"
                        color="text.disabled"
                        sx={{ ml: 0.5 }}
                      >
                        (list)
                      </Typography>
                    )}
                  </Typography>
                </Box>
              ))}
            </Box>
          </DetailSection>
        )}

        {data.actions.length > 0 && (
          <DetailSection title="Actions">
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {data.actions.map((a, i) => (
                <Box
                  key={i}
                  sx={{
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                    p: 1.5,
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                    {a.action_type}
                  </Typography>
                  {Object.keys(a.action_config).length > 0 && (
                    <Box
                      sx={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 0.25,
                      }}
                    >
                      {Object.entries(a.action_config).map(([k, v]) => (
                        <Box key={k} sx={{ display: 'flex', gap: 1 }}>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontFamily: 'monospace', minWidth: 140 }}
                          >
                            {k}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontFamily: 'monospace' }}
                          >
                            {Array.isArray(v)
                              ? (v as unknown[]).join(', ')
                              : String(v ?? '')}
                          </Typography>
                        </Box>
                      ))}
                    </Box>
                  )}
                </Box>
              ))}
            </Box>
          </DetailSection>
        )}
      </Box>
    </DetailDialog>
  );
}
