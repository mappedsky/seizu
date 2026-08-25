import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ModelProfile,
  type ModelProfilePayload,
  type ModelProfileVersion,
  type ReasoningEffort,
  useModelProfileMutations,
  useModelProfilesList,
} from 'src/hooks/useModelProfilesApi';
import { pageContentSx } from 'src/theme/layout';

const stages = [
  'assistant',
  'planner',
  'worker',
  'worker_summary',
  'sandbox_subagent',
  'synthesizer',
] as const;
const efforts: ReasoningEffort[] = [
  '',
  'none',
  'minimal',
  'low',
  'medium',
  'high',
];

function emptyPayload(): ModelProfilePayload {
  return {
    name: '',
    description: '',
    enabled: true,
    is_default: false,
    primary: { model_id: '', reasoning_effort: '' },
    economy: { model_id: '', reasoning_effort: '' },
    stage_overrides: {},
    run_cost_budget_usd: 1,
  };
}

function ProfileDialog({
  profile,
  onClose,
  onSave,
}: {
  profile: ModelProfile | null;
  onClose: () => void;
  onSave: (
    payload: ModelProfilePayload & { comment?: string },
  ) => Promise<void>;
}) {
  const [value, setValue] = useState<ModelProfilePayload>(
    profile ?? emptyPayload(),
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const updateChoice = (
    kind: 'primary' | 'economy',
    field: 'model_id' | 'reasoning_effort',
    fieldValue: string,
  ) =>
    setValue((current) => ({
      ...current,
      [kind]: { ...current[kind], [field]: fieldValue },
    }));
  const updateOverride = (
    stage: string,
    kind: 'primary' | 'economy',
    field: 'model_id' | 'reasoning_effort',
    fieldValue: string,
  ) =>
    setValue((current) => {
      const stageValue = current.stage_overrides[stage] ?? {};
      const choice = stageValue[kind] ?? {};
      let overrideValue: string | null = fieldValue || null;
      if (field === 'reasoning_effort') {
        overrideValue = fieldValue === '__inherit__' ? null : fieldValue;
      }
      const nextChoice = { ...choice, [field]: overrideValue };
      return {
        ...current,
        stage_overrides: {
          ...current.stage_overrides,
          [stage]: { ...stageValue, [kind]: nextChoice },
        },
      };
    });
  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...value,
        ...(profile ? { comment: comment || undefined } : {}),
      });
      onClose();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Failed to save model profile',
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {profile ? 'Edit model profile' : 'New model profile'}
      </DialogTitle>
      <DialogContent dividers>
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          }}
        >
          <TextField
            label="Name"
            required
            value={value.name}
            onChange={(e) => setValue({ ...value, name: e.target.value })}
          />
          <TextField
            label="Run cost cap (USD)"
            required
            type="number"
            value={value.run_cost_budget_usd}
            onChange={(e) =>
              setValue({
                ...value,
                run_cost_budget_usd: Number(e.target.value),
              })
            }
          />
          <TextField
            label="Description"
            multiline
            minRows={2}
            value={value.description}
            onChange={(e) =>
              setValue({ ...value, description: e.target.value })
            }
            sx={{ gridColumn: { md: '1 / -1' } }}
          />
          {(['primary', 'economy'] as const).map((kind) => (
            <Box key={kind} sx={{ display: 'grid', gap: 1 }}>
              <Typography variant="subtitle2">Base {kind} model</Typography>
              <TextField
                label="Model ID"
                required
                value={value[kind].model_id}
                onChange={(e) => updateChoice(kind, 'model_id', e.target.value)}
              />
              <TextField
                select
                label="Reasoning effort"
                value={value[kind].reasoning_effort}
                onChange={(e) =>
                  updateChoice(kind, 'reasoning_effort', e.target.value)
                }
              >
                {efforts.map((effort) => (
                  <MenuItem key={effort || 'provider'} value={effort}>
                    {effort || 'Provider default'}
                  </MenuItem>
                ))}
              </TextField>
            </Box>
          ))}
          <Box sx={{ gridColumn: { md: '1 / -1' } }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Stage overrides
            </Typography>
            <Typography color="text.secondary" variant="body2" sx={{ mb: 1 }}>
              Leave fields empty to inherit the base choice.
            </Typography>
            {stages.map((stage) => (
              <Box
                key={stage}
                sx={{
                  display: 'grid',
                  gap: 1,
                  gridTemplateColumns: {
                    xs: '1fr',
                    md: '150px 1fr 150px 1fr 150px',
                  },
                  mb: 1,
                  alignItems: 'center',
                }}
              >
                <Typography variant="body2">
                  {stage.replaceAll('_', ' ')}
                </Typography>
                {(['primary', 'economy'] as const).map((kind) => (
                  <Box key={kind} sx={{ display: 'contents' }}>
                    <TextField
                      size="small"
                      label={`${kind} model`}
                      value={
                        value.stage_overrides[stage]?.[kind]?.model_id ?? ''
                      }
                      onChange={(e) =>
                        updateOverride(stage, kind, 'model_id', e.target.value)
                      }
                    />
                    <TextField
                      size="small"
                      select
                      label={`${kind} effort`}
                      value={
                        value.stage_overrides[stage]?.[kind]
                          ?.reasoning_effort ?? '__inherit__'
                      }
                      onChange={(e) =>
                        updateOverride(
                          stage,
                          kind,
                          'reasoning_effort',
                          e.target.value,
                        )
                      }
                    >
                      <MenuItem value="__inherit__">Inherit</MenuItem>
                      {efforts.map((effort) => (
                        <MenuItem key={effort || 'provider'} value={effort}>
                          {effort || 'Provider default'}
                        </MenuItem>
                      ))}
                    </TextField>
                  </Box>
                ))}
              </Box>
            ))}
          </Box>
          <FormControlLabel
            control={
              <Checkbox
                checked={value.enabled}
                onChange={(e) =>
                  setValue({ ...value, enabled: e.target.checked })
                }
              />
            }
            label="Enabled"
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={value.is_default}
                onChange={(e) =>
                  setValue({ ...value, is_default: e.target.checked })
                }
              />
            }
            label="Default profile"
          />
          {profile ? (
            <TextField
              label="Version comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              sx={{ gridColumn: { md: '1 / -1' } }}
            />
          ) : null}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={
            saving ||
            !value.name.trim() ||
            !value.primary.model_id ||
            !value.economy.model_id ||
            value.run_cost_budget_usd <= 0
          }
          onClick={() => void submit()}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function ModelProfiles() {
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canRead = hasPermission('model_profiles:read');
  const canWrite = hasPermission('model_profiles:write');
  const canDelete = hasPermission('model_profiles:delete');
  const { profiles, loading, error, refresh } = useModelProfilesList(canRead);
  const mutations = useModelProfileMutations();
  const [editing, setEditing] = useState<ModelProfile | 'new' | null>(null);
  const [versions, setVersions] = useState<ModelProfileVersion[] | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  if (permissionsLoading || loading)
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  if (!canRead)
    return (
      <Box sx={pageContentSx}>
        <Typography>You do not have access to model profiles.</Typography>
      </Box>
    );
  const save = async (payload: ModelProfilePayload & { comment?: string }) => {
    if (editing === 'new') await mutations.create(payload);
    else if (editing) await mutations.update(editing.profile_id, payload);
    await refresh();
  };
  return (
    <Box sx={pageContentSx}>
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          justifyContent: 'space-between',
          mb: 2,
        }}
      >
        <Box>
          <Typography variant="h2">Model profiles</Typography>
          <Typography color="text.secondary">
            Versioned model, reasoning, and per-run spend choices for chat.
          </Typography>
        </Box>
        {canWrite ? (
          <Button
            startIcon={<AddIcon />}
            variant="contained"
            onClick={() => setEditing('new')}
          >
            New profile
          </Button>
        ) : null}
      </Box>
      {error || actionError ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {actionError ?? error}
        </Alert>
      ) : null}
      <Paper>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Primary</TableCell>
              <TableCell>Economy</TableCell>
              <TableCell>Cost cap</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {profiles.map((profile) => (
              <TableRow key={profile.profile_id}>
                <TableCell>
                  {profile.name}
                  {profile.is_default ? ' (default)' : ''}
                  <Typography
                    color="text.secondary"
                    variant="caption"
                    sx={{ display: 'block' }}
                  >
                    v{profile.current_version}
                  </Typography>
                </TableCell>
                <TableCell>{profile.primary.model_id}</TableCell>
                <TableCell>{profile.economy.model_id}</TableCell>
                <TableCell>${profile.run_cost_budget_usd}</TableCell>
                <TableCell>
                  {profile.enabled ? 'Enabled' : 'Disabled'}
                </TableCell>
                <TableCell align="right">
                  {canWrite ? (
                    <IconButton
                      aria-label={`Edit ${profile.name}`}
                      onClick={() => setEditing(profile)}
                    >
                      <EditIcon />
                    </IconButton>
                  ) : null}
                  <IconButton
                    aria-label={`History for ${profile.name}`}
                    onClick={() =>
                      void mutations
                        .versions(profile.profile_id)
                        .then(setVersions)
                        .catch((reason) => setActionError(String(reason)))
                    }
                  >
                    <HistoryIcon />
                  </IconButton>
                  {canDelete ? (
                    <IconButton
                      aria-label={`Delete ${profile.name}`}
                      onClick={() => {
                        if (
                          !window.confirm(
                            `Delete model profile “${profile.name}” and its version history?`,
                          )
                        )
                          return;
                        void mutations
                          .remove(profile.profile_id)
                          .then(refresh)
                          .catch((reason) =>
                            setActionError(
                              reason instanceof Error
                                ? reason.message
                                : String(reason),
                            ),
                          );
                      }}
                    >
                      <DeleteIcon />
                    </IconButton>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
            {profiles.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6}>
                  No model profiles are configured. Chat uses environment
                  settings until the first enabled profile is created.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </Paper>
      {editing ? (
        <ProfileDialog
          key={editing === 'new' ? 'new' : editing.profile_id}
          profile={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      ) : null}
      <Dialog
        open={versions !== null}
        onClose={() => setVersions(null)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Version history</DialogTitle>
        <DialogContent dividers>
          {versions?.map((version) => (
            <Box key={version.version} sx={{ mb: 2 }}>
              <Typography sx={{ fontWeight: 600 }}>
                Version {version.version}: {version.name}
              </Typography>
              <Typography color="text.secondary" variant="body2">
                {version.created_at}
                {version.comment ? ` — ${version.comment}` : ''}
              </Typography>
              <Typography variant="body2">
                {version.primary.model_id} / {version.economy.model_id}; $
                {version.run_cost_budget_usd}
              </Typography>
            </Box>
          ))}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setVersions(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
