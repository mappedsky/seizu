import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  InputAdornment,
  MenuItem,
  Radio,
  RadioGroup,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import type { Theme } from '@mui/material/styles';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ListViewState from 'src/components/ListViewState';
import PageTitle from 'src/components/PageTitle';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ConfiguredReasoningEffort,
  type ModelProfile,
  type ModelProfilePayload,
  type ReasoningEffort,
  useModelProfileMutations,
  useModelProfilesList,
} from 'src/hooks/useModelProfilesApi';
import { fontFamilyMono } from 'src/theme/typography';
import { modelProfilePayload } from 'src/utils/modelProfilePayload';

const LIST_PATH = '/app/model-profiles';

// Listed in the order a turn reaches them. Branches run inside a worker step.
const stages: {
  key: string;
  role: string;
  branch?: boolean;
}[] = [
  {
    key: 'router',
    role: 'Decides whether a turn is answered directly or planned',
  },
  { key: 'planner', role: 'Breaks the request into steps' },
  { key: 'worker', role: 'Carries out each plan step with tools' },
  {
    key: 'worker_summary',
    role: 'Condenses a step’s tool output',
    branch: true,
  },
  {
    key: 'sandbox_subagent',
    role: 'Separate agent for delegated subtasks, with code execution and file access in the sandbox',
  },
  { key: 'verifier', role: 'Checks each step against its success criteria' },
  { key: 'synthesizer', role: 'Writes the final answer' },
];

const efforts: ReasoningEffort[] = [
  'default',
  'none',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
];

const INHERIT = '__inherit__';

function stageLabel(stage: string): string {
  return stage.replaceAll('_', ' ');
}

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

const monoInputSx = {
  '& .MuiInputBase-input': { fontFamily: fontFamilyMono, fontSize: 14 },
  '& .MuiInputBase-input::placeholder': { opacity: 1, color: 'text.secondary' },
};

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Box component="section" sx={{ display: 'grid', gap: 2 }}>
      <Box>
        <Typography variant="h4" component="h2">
          {title}
        </Typography>
        {description ? (
          <Typography
            color="text.secondary"
            variant="body2"
            sx={{ mt: 0.5, maxWidth: '70ch' }}
          >
            {description}
          </Typography>
        ) : null}
      </Box>
      {children}
    </Box>
  );
}

const panelSx = {
  border: 1,
  borderColor: 'divider',
  borderRadius: 2,
  bgcolor: 'background.paper',
};

function ReasoningLadder({
  value,
  onToggle,
  onDefault,
}: {
  value: ModelProfilePayload;
  onToggle: (effort: ReasoningEffort, selected: boolean) => void;
  onDefault: (effort: ReasoningEffort) => void;
}) {
  const available = value.user_reasoning_efforts;
  return (
    <RadioGroup
      aria-label="Default reasoning"
      value={value.default_reasoning_effort}
      onChange={(event) => onDefault(event.target.value as ReasoningEffort)}
      sx={{
        display: 'grid',
        gap: 1,
        gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))',
      }}
    >
      {efforts.map((effort) => {
        const on = available.includes(effort);
        const isDefault = value.default_reasoning_effort === effort;
        const onlyOne = on && available.length === 1;
        return (
          <Box
            key={effort}
            sx={(theme: Theme) => ({
              ...panelSx,
              borderColor: on ? 'primary.main' : 'divider',
              bgcolor: on
                ? theme.palette.mode === 'dark'
                  ? 'rgba(143,180,255,0.08)'
                  : 'rgba(58,90,165,0.06)'
                : 'transparent',
              display: 'flex',
              flexDirection: 'column',
              gap: 0.25,
              px: 1,
              py: 0.75,
              transition:
                'border-color 150ms ease-out, background-color 150ms ease-out',
            })}
          >
            <FormControlLabel
              label={effort}
              sx={{
                m: 0,
                '& .MuiFormControlLabel-label': {
                  fontFamily: fontFamilyMono,
                  fontSize: 13,
                  fontWeight: on ? 600 : 400,
                  color: on ? 'text.primary' : 'text.secondary',
                },
              }}
              control={
                <Checkbox
                  size="small"
                  sx={{ p: 0.5, mr: 0.5 }}
                  checked={on}
                  disabled={onlyOne}
                  onChange={(event) => onToggle(effort, event.target.checked)}
                />
              }
            />
            <Box
              sx={{
                alignItems: 'center',
                display: 'flex',
                visibility: on ? 'visible' : 'hidden',
              }}
            >
              <Tooltip title={isDefault ? '' : `Start new chats on ${effort}`}>
                <Radio
                  size="small"
                  color="secondary"
                  value={effort}
                  disabled={!on}
                  icon={<StarBorderIcon fontSize="small" />}
                  checkedIcon={<StarIcon fontSize="small" />}
                  slotProps={{
                    input: { 'aria-label': `Make ${effort} the default` },
                  }}
                />
              </Tooltip>
              {isDefault ? (
                <Typography
                  variant="caption"
                  sx={{ color: 'secondary.main', fontWeight: 600 }}
                >
                  Default
                </Typography>
              ) : null}
            </Box>
          </Box>
        );
      })}
    </RadioGroup>
  );
}

function StageRouting({
  value,
  onChange,
  onReset,
}: {
  value: ModelProfilePayload;
  onChange: (
    stage: string,
    field: 'model_id' | 'reasoning_effort',
    fieldValue: string,
  ) => void;
  onReset: (stage: string) => void;
}) {
  const primary = value.primary.model_id.trim();
  const columns = {
    xs: '28px minmax(0, 1fr)',
    md: '28px minmax(180px, 1fr) minmax(0, 1.2fr) 180px 40px',
  };
  return (
    <Box sx={{ ...panelSx, overflow: 'hidden' }}>
      <Box
        aria-hidden
        sx={{
          display: { xs: 'none', md: 'grid' },
          gridTemplateColumns: columns.md,
          gap: 1.5,
          px: 2,
          py: 1,
          borderBottom: 1,
          borderColor: 'divider',
          color: 'text.secondary',
          fontSize: 12,
          fontWeight: 500,
        }}
      >
        <span />
        <span>Stage</span>
        <span>Model</span>
        <span>Reasoning</span>
        <span />
      </Box>
      <Box component="ol" sx={{ listStyle: 'none', m: 0, p: 0 }}>
        {stages.map((stage, index) => {
          const override = value.stage_overrides[stage.key] ?? {};
          const modelOverridden = !!override.model_id;
          const reasoningOverridden =
            override.reasoning_effort !== undefined &&
            override.reasoning_effort !== null;
          const overridden = modelOverridden || reasoningOverridden;
          const label = stageLabel(stage.key);
          const first = index === 0;
          const last = index === stages.length - 1;
          return (
            <Box
              component="li"
              key={stage.key}
              sx={{
                display: 'grid',
                gridTemplateColumns: columns,
                columnGap: 1.5,
                rowGap: 1,
                alignItems: 'center',
                px: 2,
                py: 1.25,
                position: 'relative',
                bgcolor: overridden ? 'action.hover' : 'transparent',
                transition: 'background-color 150ms ease-out',
              }}
            >
              {/* The route: one line threading every stage, branches stepped in. */}
              <Box
                aria-hidden
                sx={{
                  alignSelf: 'stretch',
                  gridRow: { xs: 'span 3', md: 'auto' },
                  position: 'relative',
                  mx: 'auto',
                  width: 28,
                  my: -1.25,
                }}
              >
                <Box
                  sx={{
                    position: 'absolute',
                    left: 13,
                    width: 2,
                    top: first ? '50%' : 0,
                    bottom: last ? '50%' : 0,
                    bgcolor: 'divider',
                  }}
                />
                {stage.branch ? (
                  <Box
                    sx={{
                      position: 'absolute',
                      left: 14,
                      top: '50%',
                      width: 10,
                      height: 2,
                      bgcolor: 'divider',
                    }}
                  />
                ) : null}
                <Box
                  sx={{
                    position: 'absolute',
                    top: '50%',
                    left: stage.branch ? 20 : 7,
                    width: stage.branch ? 8 : 14,
                    height: stage.branch ? 8 : 14,
                    transform: 'translateY(-50%)',
                    borderRadius: '50%',
                    border: 2,
                    borderColor: overridden ? 'primary.main' : 'text.disabled',
                    bgcolor: overridden ? 'primary.main' : 'background.paper',
                    transition:
                      'background-color 150ms ease-out, border-color 150ms ease-out',
                  }}
                />
              </Box>
              <Box sx={{ minWidth: 0, pl: stage.branch ? 1.5 : 0 }}>
                <Typography
                  variant="body2"
                  sx={{ fontWeight: 600, textTransform: 'capitalize' }}
                >
                  {label}
                </Typography>
                <Typography
                  color="text.secondary"
                  variant="caption"
                  component="p"
                >
                  {stage.role}
                </Typography>
              </Box>
              <TextField
                size="small"
                fullWidth
                value={override.model_id ?? ''}
                placeholder={primary || 'Primary model'}
                onChange={(e) =>
                  onChange(stage.key, 'model_id', e.target.value)
                }
                sx={[
                  monoInputSx,
                  { gridColumn: { xs: '2', md: 'auto' } },
                  modelOverridden
                    ? {
                        '& .MuiOutlinedInput-notchedOutline': {
                          borderColor: 'primary.main',
                        },
                      }
                    : {},
                ]}
                slotProps={{ htmlInput: { 'aria-label': `${label} model` } }}
              />
              <Box
                sx={{
                  alignItems: 'center',
                  display: 'flex',
                  gap: 0.5,
                  gridColumn: { xs: '2', md: 'span 2' },
                }}
              >
                <TextField
                  size="small"
                  select
                  fullWidth
                  value={override.reasoning_effort ?? INHERIT}
                  onChange={(e) =>
                    onChange(stage.key, 'reasoning_effort', e.target.value)
                  }
                  sx={[
                    {
                      maxWidth: { md: 180 },
                      '& .MuiSelect-select': { fontSize: 14 },
                    },
                    reasoningOverridden
                      ? {
                          '& .MuiOutlinedInput-notchedOutline': {
                            borderColor: 'primary.main',
                          },
                        }
                      : {},
                  ]}
                  slotProps={{
                    htmlInput: { 'aria-label': `${label} reasoning` },
                    select: {
                      renderValue: (selected) =>
                        selected === INHERIT ? (
                          <Box
                            component="span"
                            sx={{ color: 'text.secondary' }}
                          >
                            User’s choice
                          </Box>
                        ) : (
                          <Box
                            component="span"
                            sx={{ fontFamily: fontFamilyMono, fontSize: 14 }}
                          >
                            {String(selected)}
                          </Box>
                        ),
                    },
                  }}
                >
                  <MenuItem value={INHERIT} sx={{ color: 'text.secondary' }}>
                    User’s choice
                  </MenuItem>
                  {efforts.map((effort) => (
                    <MenuItem
                      key={effort}
                      value={effort}
                      sx={{ fontFamily: fontFamilyMono, fontSize: 13 }}
                    >
                      {effort}
                    </MenuItem>
                  ))}
                </TextField>
                <Tooltip title={overridden ? `Reset ${label} to the base` : ''}>
                  <span>
                    <IconButton
                      size="small"
                      aria-label={`Reset ${label}`}
                      disabled={!overridden}
                      onClick={() => onReset(stage.key)}
                      sx={{ visibility: overridden ? 'visible' : 'hidden' }}
                    >
                      <RestartAltIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

function ProfileEditor({
  profile,
  globalRunCostBudgetUsd,
}: {
  profile: ModelProfile | null;
  globalRunCostBudgetUsd: number;
}) {
  const navigate = useNavigate();
  const mutations = useModelProfileMutations();
  const [initial] = useState(() =>
    profile ? modelProfilePayload(profile) : emptyPayload(),
  );
  const [value, setValue] = useState<ModelProfilePayload>(initial);
  const [runCostBudgetInput, setRunCostBudgetInput] = useState(
    String(initial.run_cost_budget_usd),
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [leaveOpen, setLeaveOpen] = useState(false);

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
  const dirty =
    JSON.stringify(value) !== JSON.stringify(initial) ||
    runCostBudgetInput !== String(initial.run_cost_budget_usd) ||
    comment !== '';
  const canSave =
    !saving &&
    !!value.name.trim() &&
    !!value.primary.model_id &&
    !!value.economy.model_id &&
    runCostBudgetValid;
  const overriddenCount = stages.filter((stage) => {
    const override = value.stage_overrides[stage.key];
    return (
      !!override?.model_id ||
      (override?.reasoning_effort !== undefined &&
        override.reasoning_effort !== null)
    );
  }).length;

  const updateOverride = (
    stage: string,
    field: 'model_id' | 'reasoning_effort',
    fieldValue: string,
  ) =>
    setValue((current) => ({
      ...current,
      stage_overrides: {
        ...current.stage_overrides,
        [stage]: {
          ...(current.stage_overrides[stage] ?? {}),
          [field]: fieldValue === INHERIT ? null : fieldValue || null,
        },
      },
    }));
  const resetOverride = (stage: string) =>
    setValue((current) => {
      const next = { ...current.stage_overrides };
      delete next[stage];
      return { ...current, stage_overrides: next };
    });
  const toggleEffort = (effort: ReasoningEffort, selected: boolean) =>
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
    const payload = { ...value, run_cost_budget_usd: runCostBudgetUsd };
    try {
      if (profile) {
        await mutations.update(profile.profile_id, {
          ...payload,
          comment: comment || undefined,
        });
      } else {
        await mutations.create(payload);
      }
      navigate(LIST_PATH);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Failed to save model profile',
      );
      setSaving(false);
    }
  };
  const leave = () => {
    if (dirty) setLeaveOpen(true);
    else navigate(LIST_PATH);
  };

  const title = profile ? `Edit ${profile.name}` : 'New model profile';
  const status = profile
    ? dirty
      ? `Unsaved changes. Saving creates v${profile.current_version + 1}.`
      : `Current version v${profile.current_version}.`
    : 'Not saved yet.';

  return (
    <>
      <PageTitle>{`${title} | Seizu`}</PageTitle>
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          height: `calc(100vh - ${DASHBOARD_NAVBAR_HEIGHT}px)`,
          overflow: 'hidden',
        }}
      >
        <Box
          component="header"
          sx={{
            alignItems: { xs: 'stretch', md: 'flex-end' },
            bgcolor: 'background.default',
            borderBottom: 1,
            borderColor: 'divider',
            display: 'flex',
            flexDirection: { xs: 'column', md: 'row' },
            flexShrink: 0,
            gap: 1.5,
            justifyContent: 'space-between',
            px: { xs: 2, md: 3 },
            py: 1.5,
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Button
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={leave}
              sx={{ mb: 0.5, ml: -1 }}
            >
              Back to model profiles
            </Button>
            <Typography variant="h1" noWrap>
              {title}
            </Typography>
            <Typography color="text.secondary" variant="body2">
              {status}
            </Typography>
          </Box>
          <Box
            sx={{
              alignItems: 'center',
              display: 'flex',
              flexWrap: 'wrap',
              gap: 1,
            }}
          >
            {profile ? (
              <TextField
                size="small"
                label="Version comment"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                sx={{ width: { xs: '100%', sm: 280 } }}
              />
            ) : null}
            <Button onClick={leave} disabled={saving}>
              Cancel
            </Button>
            <Button
              variant="contained"
              disabled={!canSave}
              onClick={() => void submit()}
              sx={{ minWidth: 88 }}
            >
              {saving ? <ConstellationSpinner size={20} /> : 'Save'}
            </Button>
          </Box>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
          <Box
            sx={{
              display: 'grid',
              gap: { xs: 4, lg: 5 },
              gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) 320px' },
              maxWidth: 1440,
              px: { xs: 2, md: 3 },
              py: 3,
            }}
          >
            <Box sx={{ display: 'grid', gap: 5, alignContent: 'start' }}>
              {error ? <Alert severity="error">{error}</Alert> : null}

              <Section title="Profile">
                <Box
                  sx={{
                    display: 'grid',
                    gap: 2,
                    gridTemplateColumns: { xs: '1fr', md: '1fr 2fr' },
                  }}
                >
                  <TextField
                    label="Name"
                    required
                    value={value.name}
                    onChange={(e) =>
                      setValue({ ...value, name: e.target.value })
                    }
                  />
                  <TextField
                    label="Description"
                    multiline
                    minRows={1}
                    value={value.description}
                    onChange={(e) =>
                      setValue({ ...value, description: e.target.value })
                    }
                  />
                </Box>
              </Section>

              <Section
                title="Models"
                description="Every stage runs the primary model unless it is overridden below. Once a run nears its cost cap, eligible work switches to the economy model."
              >
                <Box
                  sx={{
                    display: 'grid',
                    gap: 2,
                    gridTemplateColumns: { xs: '1fr', md: '3fr 2fr' },
                  }}
                >
                  <Box sx={{ ...panelSx, p: 2, display: 'grid', gap: 1.5 }}>
                    <Typography variant="h6" component="h3">
                      Primary
                    </Typography>
                    <TextField
                      label="Primary model ID"
                      required
                      fullWidth
                      placeholder="anthropic/claude-sonnet-4-6"
                      value={value.primary.model_id}
                      onChange={(e) =>
                        setValue({
                          ...value,
                          primary: { model_id: e.target.value },
                        })
                      }
                      sx={monoInputSx}
                      helperText="A LiteLLM model ID. Reasoning follows the level the user picks in chat."
                    />
                  </Box>
                  <Box sx={{ ...panelSx, p: 2, display: 'grid', gap: 1.5 }}>
                    <Typography variant="h6" component="h3">
                      Economy fallback
                    </Typography>
                    <TextField
                      label="Economy model ID"
                      required
                      fullWidth
                      value={value.economy.model_id}
                      onChange={(e) =>
                        setValue({
                          ...value,
                          economy: {
                            ...value.economy,
                            model_id: e.target.value,
                          },
                        })
                      }
                      sx={monoInputSx}
                    />
                    <TextField
                      select
                      label="Economy reasoning"
                      fullWidth
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
                      {efforts.map((effort) => (
                        <MenuItem key={effort} value={effort}>
                          {effort}
                        </MenuItem>
                      ))}
                    </TextField>
                  </Box>
                </Box>
              </Section>

              <Section
                title="Reasoning users can choose"
                description="Check the levels offered in the chat selector, and star the one new conversations start on."
              >
                <ReasoningLadder
                  value={value}
                  onToggle={toggleEffort}
                  onDefault={(effort) =>
                    setValue({ ...value, default_reasoning_effort: effort })
                  }
                />
              </Section>

              <Section
                title="Stage routing"
                description={`Override the model or reasoning for one stage of a turn; an empty model runs the primary, and “User’s choice” follows the level picked in chat. ${overriddenCount === 0 ? 'Every stage currently runs the base.' : `${overriddenCount} of ${stages.length} stages are overridden.`}`}
              >
                <StageRouting
                  value={value}
                  onChange={updateOverride}
                  onReset={resetOverride}
                />
              </Section>
            </Box>

            <Box
              component="aside"
              sx={{
                alignSelf: 'start',
                display: 'grid',
                gap: 3,
                position: { lg: 'sticky' },
                top: { lg: 24 },
              }}
            >
              <Box sx={{ ...panelSx, p: 2, display: 'grid', gap: 1.5 }}>
                <Typography variant="h6" component="h2">
                  Spend
                </Typography>
                <TextField
                  label="Run cost cap (USD)"
                  required
                  type="number"
                  value={runCostBudgetInput}
                  onChange={(e) => setRunCostBudgetInput(e.target.value)}
                  error={!runCostBudgetValid}
                  helperText={
                    runCostBudgetValid
                      ? 'The most one chat turn may spend.'
                      : 'Enter an amount above $0 and up to $10,000.'
                  }
                  sx={{
                    '& .MuiInputBase-input': {
                      fontSize: 24,
                      fontWeight: 600,
                      fontVariantNumeric: 'tabular-nums',
                    },
                  }}
                  slotProps={{
                    htmlInput: { min: 0, max: 10_000, step: 'any' },
                    input: {
                      startAdornment: (
                        <InputAdornment position="start">$</InputAdornment>
                      ),
                    },
                  }}
                />
                {globalRunCostBudgetUsd > 0 ? (
                  <Typography color="text.secondary" variant="body2">
                    Deployment-wide cap: {formatUsd(globalRunCostBudgetUsd)}
                  </Typography>
                ) : null}
                {exceedsGlobalRunCostBudget ? (
                  <Alert severity="warning">
                    This profile requests {formatUsd(runCostBudgetUsd)}, but the
                    deployment-wide run cost cap is{' '}
                    {formatUsd(globalRunCostBudgetUsd)}. Turns will be limited
                    to {formatUsd(globalRunCostBudgetUsd)}.
                  </Alert>
                ) : null}
              </Box>

              <Box sx={{ ...panelSx, p: 2, display: 'grid', gap: 1 }}>
                <Typography variant="h6" component="h2">
                  Availability
                </Typography>
                <FormControlLabel
                  sx={{ alignItems: 'flex-start', m: 0 }}
                  control={
                    <Switch
                      checked={value.enabled}
                      onChange={(e) =>
                        setValue({ ...value, enabled: e.target.checked })
                      }
                    />
                  }
                  label={
                    <Box sx={{ pt: 0.75 }}>
                      <Typography variant="body2">Enabled</Typography>
                      <Typography color="text.secondary" variant="caption">
                        Users can pick this profile in chat.
                      </Typography>
                    </Box>
                  }
                />
                <FormControlLabel
                  sx={{ alignItems: 'flex-start', m: 0 }}
                  control={
                    <Switch
                      checked={value.is_default}
                      onChange={(e) =>
                        setValue({ ...value, is_default: e.target.checked })
                      }
                    />
                  }
                  label={
                    <Box sx={{ pt: 0.75 }}>
                      <Typography variant="body2">Default profile</Typography>
                      <Typography color="text.secondary" variant="caption">
                        New conversations start on this profile.
                      </Typography>
                    </Box>
                  }
                />
              </Box>
            </Box>
          </Box>
        </Box>
      </Box>
      <ConfirmDeleteDialog
        open={leaveOpen}
        title="Discard unsaved changes?"
        confirmLabel="Discard"
        onClose={() => setLeaveOpen(false)}
        onConfirm={() => navigate(LIST_PATH)}
      >
        Your edits to <strong>{value.name || 'this profile'}</strong> have not
        been saved. Leaving discards them.
      </ConfirmDeleteDialog>
    </>
  );
}

export default function ModelProfileEditor() {
  const { profileId } = useParams<{ profileId: string }>();
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canWrite = hasPermission('model_profiles:write');
  const { profiles, globalRunCostBudgetUsd, loading, error } =
    useModelProfilesList(hasPermission('model_profiles:read'));
  const profile = profileId
    ? (profiles.find((candidate) => candidate.profile_id === profileId) ?? null)
    : null;
  const ready = !permissionsLoading && !loading;
  let body: React.ReactNode = null;
  if (ready && !canWrite) {
    body = (
      <Typography>
        You do not have permission to edit model profiles.
      </Typography>
    );
  } else if (ready && profileId && !profile) {
    body = <Typography>This model profile no longer exists.</Typography>;
  }
  if (ready && !body) {
    return (
      <ProfileEditor
        key={profile?.profile_id ?? 'new'}
        profile={profile}
        globalRunCostBudgetUsd={globalRunCostBudgetUsd}
      />
    );
  }
  return (
    <Box sx={{ p: { xs: 1.5, sm: 2 } }}>
      <PageTitle>Model profiles | Seizu</PageTitle>
      <ListViewState
        loading={!ready}
        error={error}
        errorMessage="Failed to load model profiles"
      >
        {body}
      </ListViewState>
    </Box>
  );
}
