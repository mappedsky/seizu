import { memo, useState } from 'react';
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
  List,
  ListItem,
  ListItemButton,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import {
  ChatScheduleSpec,
  ScheduledChat,
  ScheduledChatRequest,
  ScheduledChatWatchScan,
  useChatSchedules,
} from 'src/hooks/useChatSchedules';

// weekday() order: 0=Monday .. 6=Sunday.
const DAY_OF_WEEK_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAYS_OF_MONTH = Array.from({ length: 31 }, (_, i) => i + 1);
const HOURS = Array.from({ length: 24 }, (_, i) => i);

function formatHour(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

interface ScheduleDialogProps {
  open: boolean;
  initial: ScheduledChat | null;
  onClose: () => void;
  onSave: (req: ScheduledChatRequest) => Promise<void>;
}

function ScheduleDialog({
  open,
  initial,
  onClose,
  onSave,
}: ScheduleDialogProps) {
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
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
                  <TextField
                    label="grouptype"
                    value={scan.grouptype ?? ''}
                    onChange={(e) =>
                      updateWatchScan(i, 'grouptype', e.target.value)
                    }
                    size="small"
                  />
                  <TextField
                    label="syncedtype"
                    value={scan.syncedtype ?? ''}
                    onChange={(e) =>
                      updateWatchScan(i, 'syncedtype', e.target.value)
                    }
                    size="small"
                  />
                  <TextField
                    label="groupid"
                    value={scan.groupid ?? ''}
                    onChange={(e) =>
                      updateWatchScan(i, 'groupid', e.target.value)
                    }
                    size="small"
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

function describeSchedule(schedule: ChatScheduleSpec): string {
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

function scheduleSubtitle(schedule: ScheduledChat): string {
  const trigger = schedule.schedule
    ? describeSchedule(schedule.schedule)
    : 'On scan updates';
  if (!schedule.enabled) return `${trigger} · disabled`;
  if (schedule.last_run_status)
    return `${trigger} · ${schedule.last_run_status}`;
  return trigger;
}

interface ChatSchedulesSectionProps {
  enabled: boolean;
}

function ChatSchedulesSection({ enabled }: ChatSchedulesSectionProps) {
  const {
    schedules,
    loading,
    error,
    createSchedule,
    updateSchedule,
    deleteSchedule,
  } = useChatSchedules(enabled);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ScheduledChat | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledChat | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteSchedule(deleteTarget.scheduled_chat_id);
      setDeleteTarget(null);
    } catch {
      setDeleteError('Failed to delete scheduled chat. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (schedule: ScheduledChat): RowMenuAction[] => [
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () => {
        setEditTarget(schedule);
        setDialogOpen(true);
      },
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteTarget(schedule);
      },
      destructive: true,
      dividerBefore: true,
    },
  ];

  return (
    <Box
      sx={{
        borderTop: 1,
        borderColor: 'divider',
        flexShrink: 0,
        maxHeight: '40%',
        overflowY: 'auto',
      }}
    >
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          justifyContent: 'space-between',
          minHeight: 36,
          px: 1.5,
        }}
      >
        <Typography
          variant="caption"
          sx={{ color: 'text.secondary', fontWeight: 700, letterSpacing: 0.8 }}
        >
          SCHEDULES
        </Typography>
        <Tooltip title="New scheduled chat" placement="right">
          <IconButton
            size="small"
            onClick={() => {
              setEditTarget(null);
              setDialogOpen(true);
            }}
            aria-label="New scheduled chat"
          >
            <AddIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 1 }}>
          <ConstellationSpinner size={20} />
        </Box>
      ) : error ? (
        <Box sx={{ color: 'text.secondary', px: 1.5, pb: 1 }}>
          <Typography variant="caption">{error}</Typography>
        </Box>
      ) : schedules.length === 0 ? (
        <Box sx={{ color: 'text.secondary', px: 1.5, pb: 1 }}>
          <Typography variant="caption">No scheduled chats yet.</Typography>
        </Box>
      ) : (
        <List dense disablePadding>
          {schedules.map((schedule) => (
            <ListItem
              key={schedule.scheduled_chat_id}
              disablePadding
              secondaryAction={
                <Box sx={{ pr: 0.5 }}>
                  <RowMenu actions={rowActions(schedule)} menuMinWidth={140} />
                </Box>
              }
            >
              <ListItemButton
                onClick={() => {
                  setEditTarget(schedule);
                  setDialogOpen(true);
                }}
                sx={{ display: 'block', pr: 5 }}
              >
                <Typography variant="body2" noWrap title={schedule.name}>
                  {schedule.name}
                </Typography>
                <Typography
                  variant="caption"
                  noWrap
                  sx={{ color: 'text.secondary', display: 'block' }}
                >
                  {scheduleSubtitle(schedule)}
                </Typography>
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      )}

      {dialogOpen ? (
        <ScheduleDialog
          key={editTarget?.scheduled_chat_id ?? 'new'}
          open={dialogOpen}
          initial={editTarget}
          onClose={() => setDialogOpen(false)}
          onSave={(req) =>
            editTarget
              ? updateSchedule(editTarget.scheduled_chat_id, req)
              : createSchedule(req)
          }
        />
      ) : null}

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void handleConfirmDelete()}
        deleting={deleting}
        error={deleteError}
      >
        Delete scheduled chat <strong>{deleteTarget?.name}</strong>? This cannot
        be undone.
      </ConfirmDeleteDialog>
    </Box>
  );
}

export default memo(ChatSchedulesSection);
