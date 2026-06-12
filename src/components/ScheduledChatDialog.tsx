import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import SyncMetadataAutocomplete from 'src/components/SyncMetadataAutocomplete';
import {
  ChatScheduleSpec,
  ScheduledChat,
  ScheduledChatRequest,
  ScheduledChatWatchScan,
} from 'src/hooks/useChatSchedules';
import { useSyncMetadataValues } from 'src/hooks/useSyncMetadataValues';

// weekday() order: 0=Monday .. 6=Sunday.
const DAY_OF_WEEK_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAYS_OF_MONTH = Array.from({ length: 31 }, (_, i) => i + 1);
const HOURS = Array.from({ length: 24 }, (_, i) => i);

function formatHour(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

export function describeSchedule(schedule: ChatScheduleSpec): string {
  if (schedule.type === 'hourly') {
    const hours = schedule.interval_hours ?? 1;
    return hours === 1 ? 'Hourly' : `Every ${hours} h`;
  }
  if (schedule.type === 'daily') {
    const days = (schedule.days_of_week ?? [])
      .map((day) => DAY_OF_WEEK_LABELS[day])
      .join(', ');
    return `${days} at ${formatHour(schedule.hour ?? 0)} UTC`;
  }
  return `Monthly on ${(schedule.days_of_month ?? []).join(', ')}`;
}

interface ScheduledChatDialogProps {
  open: boolean;
  initial: ScheduledChat | null;
  onClose: () => void;
  onSave: (req: ScheduledChatRequest) => Promise<void>;
}

function ScheduledChatDialog({
  open,
  initial,
  onClose,
  onSave,
}: ScheduledChatDialogProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [prompt, setPrompt] = useState(initial?.prompt ?? '');
  const [triggerType, setTriggerType] = useState<'schedule' | 'watch_scans'>(
    initial?.watch_scans?.length ? 'watch_scans' : 'schedule',
  );
  const [scheduleType, setScheduleType] = useState<ChatScheduleSpec['type']>(
    initial?.schedule?.type ?? 'daily',
  );
  const [intervalHours, setIntervalHours] = useState<string>(
    String(initial?.schedule?.interval_hours ?? 1),
  );
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>(
    initial?.schedule?.days_of_week ?? [],
  );
  const [hour, setHour] = useState<number>(initial?.schedule?.hour ?? 9);
  const [daysOfMonth, setDaysOfMonth] = useState<number[]>(
    initial?.schedule?.days_of_month ?? [],
  );
  const [watchScans, setWatchScans] = useState<ScheduledChatWatchScan[]>(
    initial?.watch_scans?.length ? initial.watch_scans : [{}],
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const syncValues = useSyncMetadataValues(open);

  const updateWatchScan = (
    index: number,
    field: keyof ScheduledChatWatchScan,
    value: string,
  ) => {
    setWatchScans((scans) =>
      scans.map((scan, i) =>
        i === index ? { ...scan, [field]: value || undefined } : scan,
      ),
    );
  };

  const intervalValid =
    Number.isFinite(Number(intervalHours)) && Number(intervalHours) >= 1;
  const scheduleValid =
    triggerType !== 'schedule' ||
    (scheduleType === 'hourly' && intervalValid) ||
    (scheduleType === 'daily' && daysOfWeek.length > 0) ||
    (scheduleType === 'monthly' && daysOfMonth.length > 0);
  const canSave =
    Boolean(name.trim()) && Boolean(prompt.trim()) && scheduleValid;
  const monthEndDays = daysOfMonth.filter((day) => day >= 29);

  const buildSchedule = (): ChatScheduleSpec => {
    if (scheduleType === 'hourly') {
      return { type: 'hourly', interval_hours: Number(intervalHours) };
    }
    if (scheduleType === 'daily') {
      return {
        type: 'daily',
        days_of_week: [...daysOfWeek].sort((a, b) => a - b),
        hour,
      };
    }
    return {
      type: 'monthly',
      days_of_month: [...daysOfMonth].sort((a, b) => a - b),
    };
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        name: name.trim(),
        prompt: prompt.trim(),
        schedule: triggerType === 'schedule' ? buildSchedule() : null,
        watch_scans:
          triggerType === 'watch_scans'
            ? watchScans.filter(
                (scan) => scan.grouptype || scan.syncedtype || scan.groupid,
              )
            : [],
        enabled,
        ...(initial ? { comment: comment.trim() || null } : {}),
      });
      onClose();
    } catch {
      setError('Failed to save scheduled chat. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {initial ? 'Edit scheduled chat' : 'New scheduled chat'}
      </DialogTitle>
      <DialogContent>
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
          <TextField
            autoFocus
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            size="small"
            fullWidth
          />
          <TextField
            label="Prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            size="small"
            fullWidth
            multiline
            minRows={4}
            helperText="Instructions for the agent. It runs headlessly as you, with your permissions, and can use tools to query the graph."
          />
          <FormControl>
            <FormLabel sx={{ fontSize: 13 }}>Trigger</FormLabel>
            <RadioGroup
              row
              value={triggerType}
              onChange={(e) =>
                setTriggerType(e.target.value as 'schedule' | 'watch_scans')
              }
            >
              <FormControlLabel
                value="schedule"
                control={<Radio size="small" />}
                label="Schedule"
              />
              <FormControlLabel
                value="watch_scans"
                control={<Radio size="small" />}
                label="Watch scans"
              />
            </RadioGroup>
          </FormControl>
          {triggerType === 'schedule' ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <FormControl size="small" sx={{ width: 220 }}>
                <InputLabel>Repeats</InputLabel>
                <Select
                  label="Repeats"
                  value={scheduleType}
                  onChange={(e) =>
                    setScheduleType(e.target.value as ChatScheduleSpec['type'])
                  }
                >
                  <MenuItem value="hourly">Hourly</MenuItem>
                  <MenuItem value="daily">Daily</MenuItem>
                  <MenuItem value="monthly">Monthly</MenuItem>
                </Select>
              </FormControl>
              {scheduleType === 'hourly' ? (
                <TextField
                  label="Every (hours)"
                  type="number"
                  value={intervalHours}
                  onChange={(e) => setIntervalHours(e.target.value)}
                  size="small"
                  sx={{ width: 220 }}
                  error={!intervalValid}
                  helperText={
                    intervalValid ? undefined : 'Must be at least 1 hour'
                  }
                />
              ) : null}
              {scheduleType === 'daily' ? (
                <>
                  <Box>
                    <FormLabel sx={{ fontSize: 13 }}>Days of week</FormLabel>
                    <ToggleButtonGroup
                      value={daysOfWeek}
                      onChange={(_e, value: number[]) => setDaysOfWeek(value)}
                      size="small"
                      sx={{ display: 'flex', flexWrap: 'wrap', mt: 0.5 }}
                    >
                      {DAY_OF_WEEK_LABELS.map((label, day) => (
                        <ToggleButton key={label} value={day} sx={{ px: 1.5 }}>
                          {label}
                        </ToggleButton>
                      ))}
                    </ToggleButtonGroup>
                    {daysOfWeek.length === 0 ? (
                      <Typography
                        variant="caption"
                        color="error"
                        sx={{ display: 'block', mt: 0.5 }}
                      >
                        Select at least one day
                      </Typography>
                    ) : null}
                  </Box>
                  <FormControl size="small" sx={{ width: 220 }}>
                    <InputLabel>Hour of day (UTC)</InputLabel>
                    <Select
                      label="Hour of day (UTC)"
                      value={hour}
                      onChange={(e) => setHour(Number(e.target.value))}
                    >
                      {HOURS.map((h) => (
                        <MenuItem key={h} value={h}>
                          {formatHour(h)}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </>
              ) : null}
              {scheduleType === 'monthly' ? (
                <Box>
                  <FormLabel sx={{ fontSize: 13 }}>Days of month</FormLabel>
                  <ToggleButtonGroup
                    value={daysOfMonth}
                    onChange={(_e, value: number[]) => setDaysOfMonth(value)}
                    size="small"
                    sx={{ display: 'flex', flexWrap: 'wrap', mt: 0.5 }}
                  >
                    {DAYS_OF_MONTH.map((day) => (
                      <ToggleButton
                        key={day}
                        value={day}
                        sx={{ minWidth: 40, px: 0 }}
                      >
                        {day}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                  {daysOfMonth.length === 0 ? (
                    <Typography
                      variant="caption"
                      color="error"
                      sx={{ display: 'block', mt: 0.5 }}
                    >
                      Select at least one day
                    </Typography>
                  ) : null}
                  {monthEndDays.length > 0 ? (
                    <Alert severity="warning" sx={{ mt: 1 }}>
                      Some months do not have day
                      {monthEndDays.length > 1 ? 's' : ''}{' '}
                      {monthEndDays.join(', ')}; in those months the chat runs
                      on the last day of the month instead.
                    </Alert>
                  ) : null}
                </Box>
              ) : null}
            </Box>
          ) : (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {watchScans.map((scan, i) => (
                <Box
                  key={i}
                  sx={{ alignItems: 'center', display: 'flex', gap: 1 }}
                >
                  <SyncMetadataAutocomplete
                    label="grouptype"
                    value={scan.grouptype ?? ''}
                    onChange={(value) => updateWatchScan(i, 'grouptype', value)}
                    options={syncValues.grouptypes}
                    sx={{ flex: 1 }}
                  />
                  <SyncMetadataAutocomplete
                    label="syncedtype"
                    value={scan.syncedtype ?? ''}
                    onChange={(value) =>
                      updateWatchScan(i, 'syncedtype', value)
                    }
                    options={syncValues.syncedtypes}
                    sx={{ flex: 1 }}
                  />
                  <SyncMetadataAutocomplete
                    label="groupid"
                    value={scan.groupid ?? ''}
                    onChange={(value) => updateWatchScan(i, 'groupid', value)}
                    options={syncValues.groupids}
                    sx={{ flex: 1 }}
                  />
                  <IconButton
                    size="small"
                    onClick={() =>
                      setWatchScans((scans) => scans.filter((_, j) => j !== i))
                    }
                    disabled={watchScans.length === 1}
                  >
                    <RemoveCircleOutlineIcon fontSize="small" />
                  </IconButton>
                </Box>
              ))}
              <Button
                size="small"
                startIcon={<AddCircleOutlineIcon />}
                onClick={() => setWatchScans((scans) => [...scans, {}])}
                sx={{ alignSelf: 'flex-start' }}
              >
                Add scan filter
              </Button>
            </Box>
          )}
          <FormControlLabel
            control={
              <Switch
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                size="small"
              />
            }
            label="Enabled"
          />
          {initial ? (
            <TextField
              label="Version comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              size="small"
              fullWidth
              helperText="Optional note recorded in the version history."
            />
          ) : null}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={!canSave || saving}
          onClick={() => void handleSave()}
        >
          {saving ? <ConstellationSpinner size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default ScheduledChatDialog;
