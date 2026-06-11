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
  List,
  ListItem,
  ListItemButton,
  Radio,
  RadioGroup,
  Switch,
  TextField,
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
  ScheduledChat,
  ScheduledChatRequest,
  ScheduledChatWatchScan,
  useChatSchedules,
} from 'src/hooks/useChatSchedules';

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
  const [triggerType, setTriggerType] = useState<'frequency' | 'watch_scans'>(
    initial?.watch_scans?.length ? 'watch_scans' : 'frequency',
  );
  const [frequency, setFrequency] = useState<string>(
    String(initial?.frequency ?? 1440),
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

  const frequencyValid =
    triggerType !== 'frequency' ||
    (Number.isFinite(Number(frequency)) && Number(frequency) >= 1);
  const canSave =
    Boolean(name.trim()) && Boolean(prompt.trim()) && frequencyValid;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        name: name.trim(),
        prompt: prompt.trim(),
        frequency: triggerType === 'frequency' ? Number(frequency) : null,
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
                setTriggerType(e.target.value as 'frequency' | 'watch_scans')
              }
            >
              <FormControlLabel
                value="frequency"
                control={<Radio size="small" />}
                label="Fixed frequency"
              />
              <FormControlLabel
                value="watch_scans"
                control={<Radio size="small" />}
                label="Watch scans"
              />
            </RadioGroup>
          </FormControl>
          {triggerType === 'frequency' ? (
            <TextField
              label="Frequency (minutes)"
              type="number"
              value={frequency}
              onChange={(e) => setFrequency(e.target.value)}
              size="small"
              sx={{ width: 220 }}
              error={!frequencyValid}
              helperText={
                frequencyValid ? undefined : 'Must be at least 1 minute'
              }
            />
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

function scheduleSubtitle(schedule: ScheduledChat): string {
  const trigger = schedule.frequency
    ? `Every ${schedule.frequency} min`
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
