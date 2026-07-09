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
  Radio,
  RadioGroup,
  Switch,
  TextField,
} from '@mui/material';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ScheduleSpecEditor from 'src/components/ScheduleSpecEditor';
import SyncMetadataAutocomplete from 'src/components/SyncMetadataAutocomplete';
import {
  ChatScheduleSpec,
  ScheduledChat,
  ScheduledChatRequest,
  ScheduledChatWatchScan,
} from 'src/hooks/useChatSchedules';
import { useSyncMetadataValues } from 'src/hooks/useSyncMetadataValues';

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
  const [schedule, setSchedule] = useState<ChatScheduleSpec | null>(
    initial?.schedule ?? null,
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

  const scheduleValid = triggerType !== 'schedule' || schedule !== null;
  const canSave =
    Boolean(name.trim()) && Boolean(prompt.trim()) && scheduleValid;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        name: name.trim(),
        prompt: prompt.trim(),
        schedule: triggerType === 'schedule' ? schedule : null,
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
            <ScheduleSpecEditor
              initial={initial?.schedule ?? null}
              onChange={(spec) => setSchedule(spec as ChatScheduleSpec | null)}
            />
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
