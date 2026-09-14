import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ModelProfileDetailDialog from 'src/components/ModelProfileDetailDialog';
import ListPageHeader from 'src/components/ListPageHeader';
import ListTable, {
  type ListTableColumn,
  type ListTableFilterGroup,
  listTableActionColumnSx,
  listTableMonoCellSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import PageTitle from 'src/components/PageTitle';
import RowMenu, { type RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ConfiguredReasoningEffort,
  type ModelProfile,
  type ModelProfilePayload,
  useModelProfileMutations,
  useModelProfilesList,
} from 'src/hooks/useModelProfilesApi';
import { pageContentSx } from 'src/theme/layout';
import { modelProfilePayload } from 'src/utils/modelProfilePayload';

const stages = [
  'router',
  'planner',
  'worker',
  'worker_summary',
  'sandbox_subagent',
  'verifier',
  'synthesizer',
] as const;
const efforts = [
  'default',
  'none',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
] as const;
const configuredEfforts: ConfiguredReasoningEffort[] = [
  'default',
  'none',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
];

function formatUsd(value: number): string {
  return `$${value}`;
}

function emptyPayload(): ModelProfilePayload {
  return {
    name: '',
    description: '',
    enabled: true,
    is_default: false,
    primary: { model_id: '' },
    economy: { model_id: '', reasoning_effort: 'medium' },
    stage_overrides: {},
    user_reasoning_efforts: ['low', 'medium', 'high'],
    default_reasoning_effort: 'medium',
    run_cost_budget_usd: 1,
  };
}

function editablePayload(profile: ModelProfile | null): ModelProfilePayload {
  return profile ? modelProfilePayload(profile) : emptyPayload();
}

function ProfileDialog({
  profile,
  globalRunCostBudgetUsd,
  onClose,
  onSave,
}: {
  profile: ModelProfile | null;
  globalRunCostBudgetUsd: number;
  onClose: () => void;
  onSave: (
    payload: ModelProfilePayload & { comment?: string },
  ) => Promise<void>;
}) {
  const initialValue = editablePayload(profile);
  const [value, setValue] = useState<ModelProfilePayload>(initialValue);
  const [runCostBudgetInput, setRunCostBudgetInput] = useState(
    String(initialValue.run_cost_budget_usd),
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runCostBudgetUsd = Number(runCostBudgetInput);
  const runCostBudgetValid =
    runCostBudgetInput.trim() !== '' &&
    Number.isFinite(runCostBudgetUsd) &&
    runCostBudgetUsd > 0 &&
    runCostBudgetUsd <= 10_000;
  const exceedsGlobalRunCostBudget =
    runCostBudgetValid &&
    globalRunCostBudgetUsd > 0 &&
    runCostBudgetUsd > globalRunCostBudgetUsd;
  const updateOverride = (
    stage: string,
    field: 'model_id' | 'reasoning_effort',
    fieldValue: string,
  ) =>
    setValue((current) => {
      const stageValue = current.stage_overrides[stage] ?? {};
      const nextStage = {
        ...stageValue,
        [field]: fieldValue === '__inherit__' ? null : fieldValue || null,
      };
      return {
        ...current,
        stage_overrides: {
          ...current.stage_overrides,
          [stage]: nextStage,
        },
      };
    });
  const updateUserReasoningEffort = (
    effort: ConfiguredReasoningEffort,
    selected: boolean,
  ) =>
    setValue((current) => {
      const nextEfforts = efforts.filter((candidate) =>
        candidate === effort
          ? selected
          : current.user_reasoning_efforts.includes(candidate),
      );
      if (nextEfforts.length === 0) return current;
      return {
        ...current,
        user_reasoning_efforts: nextEfforts,
        default_reasoning_effort: nextEfforts.includes(
          current.default_reasoning_effort,
        )
          ? current.default_reasoning_effort
          : nextEfforts[0],
      };
    });
  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...value,
        run_cost_budget_usd: runCostBudgetUsd,
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
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
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
            value={runCostBudgetInput}
            onChange={(e) => setRunCostBudgetInput(e.target.value)}
            slotProps={{ htmlInput: { min: 0, max: 10_000, step: 'any' } }}
          />
          {exceedsGlobalRunCostBudget ? (
            <Alert severity="warning" sx={{ gridColumn: { md: '1 / -1' } }}>
              This profile requests {formatUsd(runCostBudgetUsd)}, but the
              deployment-wide run cost cap is{' '}
              {formatUsd(globalRunCostBudgetUsd)}. Turns will be limited to{' '}
              {formatUsd(globalRunCostBudgetUsd)}.
            </Alert>
          ) : null}
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
          <Box sx={{ display: 'grid', gap: 1 }}>
            <Typography variant="subtitle2">Base primary model</Typography>
            <TextField
              label="Primary model ID"
              required
              value={value.primary.model_id}
              onChange={(e) =>
                setValue({
                  ...value,
                  primary: { model_id: e.target.value },
                })
              }
            />
            <TextField
              select
              label="Default user reasoning"
              value={value.default_reasoning_effort}
              onChange={(e) =>
                setValue({
                  ...value,
                  default_reasoning_effort: e.target
                    .value as ModelProfilePayload['default_reasoning_effort'],
                })
              }
            >
              {value.user_reasoning_efforts.map((effort) => (
                <MenuItem key={effort} value={effort}>
                  {effort}
                </MenuItem>
              ))}
            </TextField>
            <Box>
              <Typography variant="subtitle2">
                User-selectable reasoning
              </Typography>
              <FormGroup row>
                {efforts.map((effort) => (
                  <FormControlLabel
                    key={effort}
                    control={
                      <Checkbox
                        checked={value.user_reasoning_efforts.includes(effort)}
                        onChange={(event) =>
                          updateUserReasoningEffort(
                            effort,
                            event.target.checked,
                          )
                        }
                        size="small"
                      />
                    }
                    label={effort}
                  />
                ))}
              </FormGroup>
            </Box>
          </Box>
          <Box sx={{ display: 'grid', gap: 1 }}>
            <Typography variant="subtitle2">Economy fallback</Typography>
            <TextField
              label="Economy model ID"
              required
              value={value.economy.model_id}
              onChange={(e) =>
                setValue({
                  ...value,
                  economy: { ...value.economy, model_id: e.target.value },
                })
              }
            />
            <TextField
              select
              label="Economy reasoning"
              value={value.economy.reasoning_effort}
              onChange={(e) =>
                setValue({
                  ...value,
                  economy: {
                    ...value.economy,
                    reasoning_effort: e.target
                      .value as ConfiguredReasoningEffort,
                  },
                })
              }
            >
              {configuredEfforts.map((effort) => (
                <MenuItem key={effort || 'provider'} value={effort}>
                  {effort || 'Provider default'}
                </MenuItem>
              ))}
            </TextField>
          </Box>
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
                  alignItems: 'center',
                  display: 'grid',
                  gap: 1,
                  gridTemplateColumns: {
                    xs: '1fr',
                    sm: '150px 1fr 220px',
                  },
                  mb: 1,
                }}
              >
                <Typography variant="body2">
                  {stage.replaceAll('_', ' ')}
                </Typography>
                <TextField
                  size="small"
                  label={`${stage.replaceAll('_', ' ')} model`}
                  value={value.stage_overrides[stage]?.model_id ?? ''}
                  onChange={(e) =>
                    updateOverride(stage, 'model_id', e.target.value)
                  }
                />
                <TextField
                  size="small"
                  select
                  label={`${stage.replaceAll('_', ' ')} reasoning`}
                  value={
                    value.stage_overrides[stage]?.reasoning_effort ??
                    '__inherit__'
                  }
                  onChange={(e) =>
                    updateOverride(stage, 'reasoning_effort', e.target.value)
                  }
                >
                  <MenuItem value="__inherit__">
                    Inherit base (user selected)
                  </MenuItem>
                  {configuredEfforts.map((effort) => (
                    <MenuItem key={effort || 'provider'} value={effort}>
                      {effort || 'Provider default'}
                    </MenuItem>
                  ))}
                </TextField>
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
            !runCostBudgetValid
          }
          onClick={() => void submit()}
        >
          {saving ? <ConstellationSpinner size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function ModelProfiles() {
  const navigate = useNavigate();
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canRead = hasPermission('model_profiles:read');
  const canWrite = hasPermission('model_profiles:write');
  const canDelete = hasPermission('model_profiles:delete');
  const { profiles, globalRunCostBudgetUsd, loading, error, refresh } =
    useModelProfilesList(canRead);
  const mutations = useModelProfileMutations();
  const [editing, setEditing] = useState<ModelProfile | 'new' | null>(null);
  const [detail, setDetail] = useState<ModelProfile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ModelProfile | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const save = async (payload: ModelProfilePayload & { comment?: string }) => {
    if (editing === 'new') await mutations.create(payload);
    else if (editing) await mutations.update(editing.profile_id, payload);
    await refresh();
  };
  const deleteProfile = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await mutations.remove(deleteTarget.profile_id);
      setDeleteTarget(null);
      await refresh();
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
    }
  };
  const rowActions = (profile: ModelProfile): RowMenuAction[] => [
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () => setEditing(profile),
      disabled: !canWrite,
      tooltip: !canWrite
        ? 'You do not have permission to edit model profiles'
        : undefined,
    },
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(
          `/app/model-profiles/${encodeURIComponent(profile.profile_id)}/history`,
        ),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteTarget(profile);
      },
      disabled: !canDelete,
      tooltip: !canDelete
        ? 'You do not have permission to delete model profiles'
        : undefined,
      destructive: true,
      dividerBefore: true,
    },
  ];
  const columns: ListTableColumn<ModelProfile>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (profile) => (
        <Box
          sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}
        >
          <Typography
            variant="body2"
            sx={[
              {
                cursor: 'pointer',
                flex: 1,
                fontWeight: 500,
                '&:hover': { textDecoration: 'underline' },
              },
              listTableTruncateSx,
            ]}
            onClick={() => setDetail(profile)}
          >
            {profile.name}
          </Typography>
          {profile.is_default ? (
            <Chip
              label="Default"
              size="small"
              color="primary"
              variant="outlined"
              sx={{ flexShrink: 0, height: 20, fontSize: '0.7rem' }}
            />
          ) : null}
        </Box>
      ),
    },
    {
      key: 'primary',
      label: 'Primary',
      cellSx: listTableMonoCellSx,
      render: (profile) => profile.primary.model_id,
    },
    {
      key: 'economy',
      label: 'Economy',
      hideBelow: 'md',
      cellSx: listTableMonoCellSx,
      render: (profile) => profile.economy.model_id,
    },
    {
      key: 'cost',
      label: 'Cost cap',
      cellSx: { ...listTableSecondaryCellSx, width: 160 },
      render: (profile) => (
        <>
          {formatUsd(profile.run_cost_budget_usd)}
          {globalRunCostBudgetUsd > 0 &&
          profile.run_cost_budget_usd > globalRunCostBudgetUsd ? (
            <Typography
              color="warning.main"
              variant="caption"
              sx={{ display: 'block' }}
            >
              Limited to {formatUsd(globalRunCostBudgetUsd)} globally
            </Typography>
          ) : null}
        </>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: { width: 120 },
      render: (profile) => (
        <Chip
          label={profile.enabled ? 'Enabled' : 'Disabled'}
          color={profile.enabled ? 'success' : 'default'}
          size="small"
        />
      ),
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: { ...listTableSecondaryCellSx, width: 96 },
      render: (profile) => `v${profile.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Latest Update',
      hideBelow: 'xl',
      cellSx: { ...listTableSecondaryCellSx, width: 180 },
      render: (profile) => new Date(profile.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated By',
      hideBelow: 'lg',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (profile) => (
        <UserDisplay userId={profile.updated_by ?? profile.created_by} />
      ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      textual: false,
      render: (profile) => (
        <RowMenu
          label={`Actions for ${profile.name}`}
          actions={rowActions(profile)}
        />
      ),
    },
  ];
  const filterGroups: ListTableFilterGroup<ModelProfile>[] = [
    {
      key: 'status',
      label: 'Status',
      icon: <ToggleOnIcon fontSize="small" />,
      options: [
        {
          key: 'enabled',
          label: 'Enabled',
          icon: <ToggleOnIcon fontSize="small" />,
          matches: (profile) => profile.enabled,
        },
        {
          key: 'disabled',
          label: 'Disabled',
          icon: <ToggleOffIcon fontSize="small" />,
          matches: (profile) => !profile.enabled,
        },
      ],
    },
  ];
  return (
    <Box sx={pageContentSx}>
      <PageTitle>Model profiles | Seizu</PageTitle>
      <ListPageHeader
        title="Model profiles"
        action={
          canWrite && !permissionsLoading ? (
            <Button
              startIcon={<AddIcon />}
              variant="contained"
              onClick={() => setEditing('new')}
            >
              New profile
            </Button>
          ) : null
        }
      />
      <ListViewState
        loading={permissionsLoading || loading}
        error={error}
        errorMessage="Failed to load model profiles"
      >
        {canRead ? (
          <ListTable
            rows={profiles}
            columns={columns}
            getRowKey={(profile) => profile.profile_id}
            filterGroups={filterGroups}
            emptyMessage="No model profiles are configured. Chat uses environment settings until the first enabled profile is created."
          />
        ) : (
          <Typography>You do not have access to model profiles.</Typography>
        )}
      </ListViewState>
      <ConfirmDeleteDialog
        open={deleteTarget !== null}
        title="Delete model profile?"
        deleting={deleting}
        error={deleteError}
        onClose={() => {
          if (!deleting) setDeleteTarget(null);
        }}
        onConfirm={() => void deleteProfile()}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and its version
        history? This cannot be undone.
      </ConfirmDeleteDialog>
      <ModelProfileDetailDialog
        profile={detail}
        version={detail?.current_version}
        onClose={() => setDetail(null)}
      />
      {editing ? (
        <ProfileDialog
          key={editing === 'new' ? 'new' : editing.profile_id}
          profile={editing === 'new' ? null : editing}
          globalRunCostBudgetUsd={globalRunCostBudgetUsd}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      ) : null}
    </Box>
  );
}
