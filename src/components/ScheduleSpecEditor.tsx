import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  FormControl,
  FormLabel,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import {
  DAY_OF_WEEK_LABELS,
  ScheduleSpec,
  formatTimeOfDay,
} from 'src/scheduleSpec';

const DAYS_OF_MONTH = Array.from({ length: 31 }, (_, i) => i + 1);
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5);

export interface ScheduleSpecEditorProps {
  // Seed values for a new editor instance (remount with a `key` to reset).
  initial: ScheduleSpec | null;
  // Fired with the built spec, or null while the current input is invalid.
  onChange: (spec: ScheduleSpec | null) => void;
  // Adds the minute-granularity surface: the 'interval' (every N minutes)
  // repeat type and a minute-of-hour selector for daily schedules.
  // Scheduled chats leave this off (hourly granularity).
  allowMinutes?: boolean;
  // Hour preselected for daily schedules when creating from scratch.
  defaultHour?: number;
}

// Shared editor for ScheduleSpec triggers (scheduled queries + scheduled
// chats). Owns the repeat-type selector and the per-type fields; reports the
// built spec (or null while invalid) through onChange.
function ScheduleSpecEditor({
  initial,
  onChange,
  allowMinutes = false,
  defaultHour = 9,
}: ScheduleSpecEditorProps) {
  const [scheduleType, setScheduleType] = useState<ScheduleSpec['type']>(
    initial?.type ?? 'daily',
  );
  const [intervalMinutes, setIntervalMinutes] = useState<string>(
    String(initial?.interval_minutes ?? 60),
  );
  const [intervalHours, setIntervalHours] = useState<string>(
    String(initial?.interval_hours ?? 1),
  );
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>(
    initial?.days_of_week ?? [],
  );
  const [hour, setHour] = useState<number>(initial?.hour ?? defaultHour);
  const [minute, setMinute] = useState<number>(initial?.minute ?? 0);
  const [daysOfMonth, setDaysOfMonth] = useState<number[]>(
    initial?.days_of_month ?? [],
  );

  const intervalMinutesValid =
    Number.isFinite(Number(intervalMinutes)) && Number(intervalMinutes) >= 1;
  const intervalHoursValid =
    Number.isFinite(Number(intervalHours)) && Number(intervalHours) >= 1;
  const monthEndDays = daysOfMonth.filter((day) => day >= 29);

  const buildSpec = (): ScheduleSpec | null => {
    if (scheduleType === 'interval') {
      return intervalMinutesValid
        ? { type: 'interval', interval_minutes: Number(intervalMinutes) }
        : null;
    }
    if (scheduleType === 'hourly') {
      return intervalHoursValid
        ? { type: 'hourly', interval_hours: Number(intervalHours) }
        : null;
    }
    if (scheduleType === 'daily') {
      if (daysOfWeek.length === 0) return null;
      return {
        type: 'daily',
        days_of_week: [...daysOfWeek].sort((a, b) => a - b),
        hour,
        ...(allowMinutes ? { minute } : {}),
      };
    }
    if (daysOfMonth.length === 0) return null;
    return {
      type: 'monthly',
      days_of_month: [...daysOfMonth].sort((a, b) => a - b),
    };
  };

  // Report the current spec upward without requiring the parent to memoize
  // its callback.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const specJson = JSON.stringify(buildSpec());
  useEffect(() => {
    onChangeRef.current(JSON.parse(specJson) as ScheduleSpec | null);
  }, [specJson]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <FormControl size="small" sx={{ width: 220 }}>
        <InputLabel>Repeats</InputLabel>
        <Select
          label="Repeats"
          value={scheduleType}
          onChange={(e) =>
            setScheduleType(e.target.value as ScheduleSpec['type'])
          }
        >
          {allowMinutes ? (
            <MenuItem value="interval">Every N minutes</MenuItem>
          ) : null}
          <MenuItem value="hourly">Hourly</MenuItem>
          <MenuItem value="daily">Daily</MenuItem>
          <MenuItem value="monthly">Monthly</MenuItem>
        </Select>
      </FormControl>
      {scheduleType === 'interval' ? (
        <TextField
          label="Every (minutes)"
          type="number"
          value={intervalMinutes}
          onChange={(e) => setIntervalMinutes(e.target.value)}
          size="small"
          sx={{ width: 220 }}
          error={!intervalMinutesValid}
          helperText={
            intervalMinutesValid ? undefined : 'Must be at least 1 minute'
          }
        />
      ) : null}
      {scheduleType === 'hourly' ? (
        <TextField
          label="Every (hours)"
          type="number"
          value={intervalHours}
          onChange={(e) => setIntervalHours(e.target.value)}
          size="small"
          sx={{ width: 220 }}
          error={!intervalHoursValid}
          helperText={
            intervalHoursValid ? undefined : 'Must be at least 1 hour'
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
          <Box sx={{ display: 'flex', gap: 2 }}>
            <FormControl size="small" sx={{ width: 220 }}>
              <InputLabel>Hour of day (UTC)</InputLabel>
              <Select
                label="Hour of day (UTC)"
                value={hour}
                onChange={(e) => setHour(Number(e.target.value))}
              >
                {HOURS.map((h) => (
                  <MenuItem key={h} value={h}>
                    {formatTimeOfDay(h, 0)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {allowMinutes ? (
              <FormControl size="small" sx={{ width: 140 }}>
                <InputLabel>Minute</InputLabel>
                <Select
                  label="Minute"
                  value={minute}
                  onChange={(e) => setMinute(Number(e.target.value))}
                >
                  {MINUTES.map((m) => (
                    <MenuItem key={m} value={m}>
                      :{String(m).padStart(2, '0')}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            ) : null}
          </Box>
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
              <ToggleButton key={day} value={day} sx={{ minWidth: 40, px: 0 }}>
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
              {monthEndDays.length > 1 ? 's' : ''} {monthEndDays.join(', ')}; in
              those months the run happens on the last day of the month instead.
            </Alert>
          ) : null}
        </Box>
      ) : null}
    </Box>
  );
}

export default ScheduleSpecEditor;
