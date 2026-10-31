import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  FormControlLabel,
  IconButton,
  InputAdornment,
  InputBase,
  MenuItem,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ListViewState from 'src/components/ListViewState';
import PageTitle from 'src/components/PageTitle';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';
import { useLazyCypherQuery } from 'src/hooks/useCypherQuery';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type CreateToolRequest,
  type ToolItem,
  type ToolParamDef,
  type UpdateToolRequest,
  useToolMutations,
  useToolsList,
} from 'src/hooks/useToolsetsApi';
import { fontFamilyMono } from 'src/theme/typography';
import { cypherParameterNames } from 'src/utils/cypherParameters';

const LOWER_SNAKE_ID = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
// Cap each slug component so the full "toolset__tool" MCP name stays <= 64 chars
// (31 + "__" + 31). Mirrors MAX_SLUG_COMPONENT_LEN in mcp_config.py.
const MAX_SLUG_LEN = 31;
// Draft results past this many rows are counted, not rendered.
const MAX_RESULT_ROWS = 100;
const RUN_SHORTCUT = /Mac|iPhone|iPad/.test(navigator.platform)
  ? '⌘↵'
  : 'Ctrl+↵';

const paramTypes: ToolParamDef['type'][] = [
  'string',
  'integer',
  'float',
  'boolean',
];

const isReadOnlyToolset = (id: string): boolean =>
  (id.startsWith('__builtin_') || id.startsWith('__external_')) &&
  id.endsWith('__');

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

type ParamDraft = {
  key: number;
  name: string;
  type: ToolParamDef['type'];
  description: string;
  required: boolean;
  default_str: string;
};

type ToolDraft = {
  tool_id: string;
  name: string;
  description: string;
  cypher: string;
  enabled: boolean;
  params: ParamDraft[];
};

let nextParamKey = 0;

function paramDraft(p: Partial<ToolParamDef> = {}): ParamDraft {
  nextParamKey += 1;
  return {
    key: nextParamKey,
    name: p.name ?? '',
    type: p.type ?? 'string',
    description: p.description ?? '',
    required: p.required ?? true,
    default_str:
      p.default !== null && p.default !== undefined ? String(p.default) : '',
  };
}

function toolDraft(tool: ToolItem | null): ToolDraft {
  return {
    tool_id: tool?.tool_id ?? '',
    name: tool?.name ?? '',
    description: tool?.description ?? '',
    cypher: tool?.cypher ?? '',
    enabled: tool?.enabled ?? true,
    params: (tool?.parameters ?? []).map((p) => paramDraft(p)),
  };
}

/** The draft without its React keys, for comparing two drafts. */
function draftSnapshot(draft: ToolDraft): string {
  return JSON.stringify({
    ...draft,
    params: draft.params.map(({ key: _key, ...rest }) => rest),
  });
}

/**
 * Parse a typed value as a parameter of `type`. An empty string is "no value"
 * (`undefined`); an unparseable one is `{ error }`.
 */
function parseValue(
  type: ToolParamDef['type'],
  raw: string,
): { value: unknown } | { error: string } {
  const text = raw.trim();
  if (text === '') return { value: undefined };
  if (type === 'integer') {
    return /^-?\d+$/.test(text)
      ? { value: parseInt(text, 10) }
      : { error: 'Enter a whole number' };
  }
  if (type === 'float') {
    const n = Number(text);
    return text !== '' && Number.isFinite(n)
      ? { value: n }
      : { error: 'Enter a number' };
  }
  if (type === 'boolean') {
    if (text === 'true' || text === 'false') return { value: text === 'true' };
    return { error: 'Choose true or false' };
  }
  return { value: text };
}

function parsedDefault(p: ParamDraft): unknown {
  const parsed = parseValue(p.type, p.default_str);
  return 'value' in parsed && parsed.value !== undefined ? parsed.value : null;
}

function toParamDef(p: ParamDraft): ToolParamDef {
  return {
    name: p.name.trim(),
    type: p.type,
    description: p.description.trim(),
    required: p.required,
    default: parsedDefault(p),
  };
}

/** Required with no default is the only case an agent must supply a value. */
function mustSupply(p: ParamDraft): boolean {
  return p.required && parsedDefault(p) === null;
}

function paramNameError(p: ParamDraft, all: ParamDraft[]): string | null {
  const name = p.name.trim();
  if (name === '') return 'Name the parameter';
  if (!LOWER_SNAKE_ID.test(name)) return 'Use lower_snake_case';
  if (all.some((other) => other !== p && other.name.trim() === name))
    return 'Another parameter has this name';
  return null;
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

const panelSx = {
  border: 1,
  borderColor: 'divider',
  borderRadius: 2,
  bgcolor: 'background.paper',
};

const monoInputSx = {
  '& .MuiInputBase-input': { fontFamily: fontFamilyMono, fontSize: 14 },
};

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box component="section" sx={{ display: 'grid', gap: 2 }}>
      <Box
        sx={{
          alignItems: 'flex-end',
          display: 'flex',
          gap: 2,
          justifyContent: 'space-between',
        }}
      >
        <Box sx={{ minWidth: 0 }}>
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
        {action}
      </Box>
      {children}
    </Box>
  );
}

/**
 * The tool as an agent sees it: its callable name, and the arguments it takes
 * with their types and defaults.
 */
function Signature({
  callable,
  params,
}: {
  callable: string;
  params: ParamDraft[];
}) {
  const named = params.filter((p) => p.name.trim() !== '');
  const multiline = named.length > 2;
  const punct = { color: 'text.secondary' };
  const args = named.map((p, index) => {
    const def = parsedDefault(p);
    return (
      <Box
        component="span"
        key={p.key}
        sx={multiline ? { display: 'block', pl: 3 } : undefined}
      >
        <Box component="span" sx={{ color: 'text.primary' }}>
          {p.name.trim()}
        </Box>
        {mustSupply(p) ? null : (
          <Box component="span" sx={punct}>
            ?
          </Box>
        )}
        <Box component="span" sx={punct}>
          :{' '}
        </Box>
        <Box component="span" sx={{ color: 'tertiary.main' }}>
          {p.type}
        </Box>
        {def === null ? null : (
          <>
            <Box component="span" sx={punct}>
              {' = '}
            </Box>
            <Box component="span" sx={{ color: 'secondary.main' }}>
              {p.type === 'string' ? JSON.stringify(def) : String(def)}
            </Box>
          </>
        )}
        {multiline || index < named.length - 1 ? (
          <Box component="span" sx={punct}>
            {multiline ? ',' : ', '}
          </Box>
        ) : null}
      </Box>
    );
  });
  return (
    <Box
      data-testid="tool-signature"
      sx={{
        fontFamily: fontFamilyMono,
        fontSize: { xs: 15, md: 18 },
        lineHeight: 1.6,
        overflowWrap: 'anywhere',
      }}
    >
      <Box component="span" sx={{ color: 'primary.main', fontWeight: 600 }}>
        {callable}
      </Box>
      <Box component="span" sx={punct}>
        (
      </Box>
      {args}
      <Box component="span" sx={punct}>
        )
      </Box>
    </Box>
  );
}

function ParamRow({
  param,
  nameError,
  referenced,
  onChange,
  onRemove,
}: {
  param: ParamDraft;
  nameError: string | null;
  referenced: boolean;
  onChange: (patch: Partial<ParamDraft>) => void;
  onRemove: () => void;
}) {
  const label = param.name.trim() || 'parameter';
  const defaultParse = parseValue(param.type, param.default_str);
  const defaultError = 'error' in defaultParse ? defaultParse.error : null;
  const showNameError = nameError !== null && param.name.trim() !== '';
  return (
    <Box
      component="li"
      sx={{
        display: 'grid',
        gap: 1.5,
        gridTemplateColumns: {
          xs: 'minmax(0, 1fr) 40px',
          md: 'minmax(160px, 1.2fr) 130px minmax(120px, 1fr) 120px 40px',
        },
        alignItems: 'start',
        px: 2,
        py: 1.75,
        '& + &': { borderTop: 1, borderColor: 'divider' },
      }}
    >
      <TextField
        size="small"
        label="Name"
        value={param.name}
        onChange={(e) => onChange({ name: e.target.value })}
        error={showNameError}
        helperText={
          showNameError
            ? nameError
            : !referenced && param.name.trim() !== ''
              ? `No $${param.name.trim()} in the query`
              : undefined
        }
        sx={[
          monoInputSx,
          !showNameError && !referenced && param.name.trim() !== ''
            ? { '& .MuiFormHelperText-root': { color: 'warning.main' } }
            : {},
        ]}
        slotProps={{
          htmlInput: { spellCheck: false, 'aria-label': 'Parameter name' },
          input: {
            startAdornment: (
              <InputAdornment position="start" sx={{ mr: 0.25 }}>
                <Box
                  component="span"
                  sx={{ fontFamily: fontFamilyMono, color: 'text.secondary' }}
                >
                  $
                </Box>
              </InputAdornment>
            ),
          },
        }}
      />
      <IconButton
        aria-label={`Remove ${label}`}
        onClick={onRemove}
        sx={{ gridColumn: { xs: 2, md: 5 }, gridRow: 1, mt: 0.25 }}
      >
        <CloseIcon fontSize="small" />
      </IconButton>
      <TextField
        size="small"
        select
        label="Type"
        value={param.type}
        onChange={(e) =>
          onChange({
            type: e.target.value as ToolParamDef['type'],
            default_str: '',
          })
        }
        sx={[monoInputSx, { gridColumn: { xs: 1, md: 'auto' } }]}
      >
        {paramTypes.map((type) => (
          <MenuItem
            key={type}
            value={type}
            sx={{ fontFamily: fontFamilyMono, fontSize: 13 }}
          >
            {type}
          </MenuItem>
        ))}
      </TextField>
      {param.type === 'boolean' ? (
        <TextField
          size="small"
          select
          label="Default"
          value={param.default_str}
          onChange={(e) => onChange({ default_str: e.target.value })}
          sx={[monoInputSx, { gridColumn: { xs: 1, md: 'auto' } }]}
          slotProps={{
            select: {
              displayEmpty: true,
              renderValue: (selected) =>
                selected === '' ? (
                  <Box component="span" sx={{ color: 'text.secondary' }}>
                    None
                  </Box>
                ) : (
                  String(selected)
                ),
            },
            inputLabel: { shrink: true },
          }}
        >
          <MenuItem value="" sx={{ color: 'text.secondary' }}>
            None
          </MenuItem>
          <MenuItem value="true">true</MenuItem>
          <MenuItem value="false">false</MenuItem>
        </TextField>
      ) : (
        <TextField
          size="small"
          label="Default"
          value={param.default_str}
          placeholder="None"
          onChange={(e) => onChange({ default_str: e.target.value })}
          error={defaultError !== null}
          helperText={defaultError ?? undefined}
          sx={[monoInputSx, { gridColumn: { xs: 1, md: 'auto' } }]}
          slotProps={{
            inputLabel: { shrink: true },
            htmlInput: {
              spellCheck: false,
              inputMode:
                param.type === 'integer'
                  ? 'numeric'
                  : param.type === 'float'
                    ? 'decimal'
                    : undefined,
            },
          }}
        />
      )}
      <FormControlLabel
        label="Required"
        sx={{ gridColumn: { xs: 1, md: 'auto' }, m: 0, height: 40 }}
        control={
          <Switch
            size="small"
            checked={param.required}
            onChange={(e) => onChange({ required: e.target.checked })}
          />
        }
      />
      <TextField
        size="small"
        label="Description"
        placeholder="What the agent should pass, e.g. “Severity to filter by: critical, high, medium or low”"
        value={param.description}
        onChange={(e) => onChange({ description: e.target.value })}
        sx={{ gridColumn: { xs: 1, md: '1 / 5' } }}
        slotProps={{ inputLabel: { shrink: true } }}
      />
    </Box>
  );
}

/**
 * Runs the unsaved query with the arguments an agent would send: what the
 * caller types, else the parameter's default, else null.
 */
function useDraftRun(cypher: string, params: ParamDraft[], canRun: boolean) {
  const [inputs, setInputs] = useState<Record<number, string>>({});
  const [lastRun, setLastRun] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [run, state] = useLazyCypherQuery(cypher.trim() || undefined);
  const named = params.filter((p) => p.name.trim() !== '');

  const argProblems = new Map<number, string>();
  const args: Record<string, unknown> = {};
  for (const p of named) {
    const raw = inputs[p.key] ?? '';
    const parsed = parseValue(p.type, raw);
    if ('error' in parsed) {
      argProblems.set(p.key, parsed.error);
    } else if (parsed.value !== undefined) {
      args[p.name.trim()] = parsed.value;
    } else if (mustSupply(p)) {
      argProblems.set(p.key, 'Required');
    } else {
      args[p.name.trim()] = parsedDefault(p);
    }
  }
  const ready = canRun && cypher.trim() !== '' && argProblems.size === 0;
  const runKey = JSON.stringify({ cypher: cypher.trim(), args });
  const stale = lastRun !== null && lastRun !== runKey;

  const start = () => {
    setTouched(true);
    if (!ready) return;
    setLastRun(runKey);
    run(args);
  };
  const setInput = (key: number, value: string) =>
    setInputs((current) => ({ ...current, [key]: value }));

  return {
    named,
    inputs,
    setInput,
    argProblems: touched ? argProblems : new Map<number, string>(),
    lastRun,
    stale,
    start,
    state,
  };
}

type DraftRun = ReturnType<typeof useDraftRun>;

function TryIt({
  canRun,
  cypher,
  draftRun,
}: {
  canRun: boolean;
  cypher: string;
  draftRun: DraftRun;
}) {
  const { named, inputs, setInput, argProblems, lastRun, stale, start, state } =
    draftRun;
  const records = state.records;
  const hasOutcome =
    lastRun !== null &&
    !state.loading &&
    (records !== undefined ||
      state.error !== null ||
      state.queryErrors.length > 0);

  return (
    <Box sx={{ ...panelSx, display: 'grid', gap: 2, p: 2 }}>
      <Box>
        <Typography variant="h6" component="h2">
          Try it
        </Typography>
        <Typography color="text.secondary" variant="body2" sx={{ mt: 0.25 }}>
          Runs this draft, read-only, with the arguments an agent would send.
        </Typography>
      </Box>
      {!canRun ? (
        <Typography color="text.secondary" variant="body2">
          Running a draft needs the <code>query:execute</code> permission.
        </Typography>
      ) : (
        <>
          {named.length === 0 ? null : (
            <Box sx={{ display: 'grid', gap: 2.25 }}>
              {named.map((p) => {
                const def = parsedDefault(p);
                const problem = argProblems.get(p.key);
                const common = {
                  size: 'small' as const,
                  fullWidth: true,
                  label: p.name.trim(),
                  value: inputs[p.key] ?? '',
                  error: problem !== undefined,
                  helperText:
                    problem ??
                    (def === null
                      ? p.type
                      : `${p.type}, default ${String(def)}`),
                  sx: [
                    monoInputSx,
                    {
                      '& .MuiInputLabel-root': { fontFamily: fontFamilyMono },
                      '& .MuiInputBase-input::placeholder': {
                        color: 'text.secondary',
                        opacity: 1,
                      },
                    },
                  ],
                };
                const onValue = (value: string) => setInput(p.key, value);
                return p.type === 'boolean' ? (
                  <TextField
                    key={p.key}
                    {...common}
                    select
                    onChange={(e) => onValue(e.target.value)}
                    slotProps={{
                      select: {
                        displayEmpty: true,
                        renderValue: (selected) =>
                          selected === '' ? (
                            <Box
                              component="span"
                              sx={{ color: 'text.secondary' }}
                            >
                              {def === null ? 'null' : String(def)}
                            </Box>
                          ) : (
                            String(selected)
                          ),
                      },
                      inputLabel: { shrink: true },
                    }}
                  >
                    <MenuItem value="" sx={{ color: 'text.secondary' }}>
                      {def === null ? 'Not set' : `Default (${String(def)})`}
                    </MenuItem>
                    <MenuItem value="true">true</MenuItem>
                    <MenuItem value="false">false</MenuItem>
                  </TextField>
                ) : (
                  <TextField
                    key={p.key}
                    {...common}
                    placeholder={def === null ? undefined : String(def)}
                    onChange={(e) => onValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') start();
                    }}
                    slotProps={{
                      inputLabel: { shrink: true },
                      htmlInput: { spellCheck: false },
                    }}
                  />
                );
              })}
            </Box>
          )}
          <Box sx={{ alignItems: 'center', display: 'flex', gap: 1.5 }}>
            <Button
              variant="outlined"
              startIcon={
                state.loading && lastRun !== null ? (
                  <ConstellationSpinner size={16} />
                ) : (
                  <PlayArrowIcon />
                )
              }
              onClick={start}
              disabled={cypher.trim() === '' || state.loading}
            >
              Run draft
            </Button>
            <Typography color="text.secondary" variant="caption">
              {RUN_SHORTCUT} in the query
            </Typography>
          </Box>
          {hasOutcome ? (
            <Box sx={{ display: 'grid', gap: 1 }} aria-live="polite">
              {state.error ? (
                <Alert severity="error">{state.error.message}</Alert>
              ) : null}
              {state.queryErrors.length > 0 ? (
                <Alert severity="error">
                  The query did not run: {state.queryErrors.join(' ')}
                </Alert>
              ) : null}
              {state.warnings.length > 0 ? (
                <Alert severity="warning">{state.warnings.join(' ')}</Alert>
              ) : null}
              {records !== undefined && !state.error ? (
                <>
                  <Typography
                    variant="body2"
                    sx={{
                      fontWeight: 600,
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {records.length === 1
                      ? '1 row'
                      : `${records.length.toLocaleString()} rows`}
                    {stale ? (
                      <Box
                        component="span"
                        sx={{ color: 'warning.main', fontWeight: 400, ml: 1 }}
                      >
                        from an earlier draft
                      </Box>
                    ) : null}
                  </Typography>
                  {records.length > 0 ? (
                    <Box
                      component="pre"
                      data-testid="try-it-results"
                      sx={{
                        bgcolor: 'action.hover',
                        borderRadius: 1,
                        fontFamily: fontFamilyMono,
                        fontSize: 12,
                        lineHeight: 1.5,
                        m: 0,
                        maxHeight: 360,
                        opacity: stale ? 0.6 : 1,
                        overflow: 'auto',
                        p: 1.5,
                        transition: 'opacity 150ms ease-out',
                      }}
                    >
                      {JSON.stringify(
                        records.slice(0, MAX_RESULT_ROWS),
                        null,
                        2,
                      )}
                    </Box>
                  ) : (
                    <Typography color="text.secondary" variant="body2">
                      The query matched nothing with these arguments.
                    </Typography>
                  )}
                  {records.length > MAX_RESULT_ROWS ? (
                    <Typography color="text.secondary" variant="caption">
                      Showing the first {MAX_RESULT_ROWS}.
                    </Typography>
                  ) : null}
                </>
              ) : null}
            </Box>
          ) : null}
        </>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

function Editor({
  toolsetId,
  tool,
}: {
  toolsetId: string;
  tool: ToolItem | null;
}) {
  const navigate = useNavigate();
  const mutations = useToolMutations(toolsetId);
  const { hasPermission } = usePermissionState();
  const listPath = `/app/toolsets/${toolsetId}/tools`;
  const [initial] = useState(() => toolDraft(tool));
  const [draft, setDraft] = useState(initial);
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [leaveOpen, setLeaveOpen] = useState(false);

  const canRun = hasPermission('query:execute');
  const draftRun = useDraftRun(draft.cypher, draft.params, canRun);
  const referenced = useMemo(
    () => cypherParameterNames(draft.cypher),
    [draft.cypher],
  );
  const declared = new Set(draft.params.map((p) => p.name.trim()));
  const undeclared = referenced.filter((name) => !declared.has(name));

  const toolId = tool ? tool.tool_id : draft.tool_id.trim();
  const toolIdError = tool
    ? null
    : toolId === ''
      ? null
      : !LOWER_SNAKE_ID.test(toolId)
        ? 'Use lower_snake_case, starting with a letter'
        : toolId.length > MAX_SLUG_LEN
          ? `At most ${MAX_SLUG_LEN} characters`
          : null;
  const paramErrors = draft.params.map((p) => paramNameError(p, draft.params));
  const defaultsValid = draft.params.every(
    (p) => !('error' in parseValue(p.type, p.default_str)),
  );
  const missing = [
    !tool && toolId === '' ? 'an ID' : null,
    draft.name.trim() === '' ? 'a name' : null,
    draft.cypher.trim() === '' ? 'a query' : null,
  ].filter((item): item is string => item !== null);
  const valid =
    missing.length === 0 &&
    toolIdError === null &&
    paramErrors.every((e) => e === null) &&
    defaultsValid;
  const dirty =
    draftSnapshot(draft) !== draftSnapshot(initial) || comment !== '';
  const canSave = valid && !saving && (dirty || !tool);

  const update = (patch: Partial<ToolDraft>) =>
    setDraft((current) => ({ ...current, ...patch }));
  const updateParam = (key: number, patch: Partial<ParamDraft>) =>
    setDraft((current) => ({
      ...current,
      params: current.params.map((p) =>
        p.key === key ? { ...p, ...patch } : p,
      ),
    }));
  const addParam = (name = '') =>
    setDraft((current) => ({
      ...current,
      params: [...current.params, paramDraft({ name })],
    }));
  const removeParam = (key: number) =>
    setDraft((current) => ({
      ...current,
      params: current.params.filter((p) => p.key !== key),
    }));

  const submit = async () => {
    setSaving(true);
    setError(null);
    const body = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      cypher: draft.cypher.trim(),
      parameters: draft.params.map(toParamDef),
      enabled: draft.enabled,
    };
    try {
      if (tool) {
        const req: UpdateToolRequest = {
          ...body,
          comment: comment.trim() || null,
        };
        await mutations.updateTool(tool.tool_id, req);
      } else {
        const req: CreateToolRequest = { ...body, tool_id: toolId };
        await mutations.createTool(req);
      }
      navigate(listPath);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Failed to save tool',
      );
      setSaving(false);
    }
  };
  const leave = () => {
    if (dirty) setLeaveOpen(true);
    else navigate(listPath);
  };

  const title = tool ? `Edit ${tool.name}` : 'New tool';
  let status: string;
  if (missing.length > 0) {
    status = `Needs ${missing.join(', ').replace(/, ([^,]*)$/, ' and $1')} before it can be saved.`;
  } else if (!tool) {
    status = 'Not saved yet.';
  } else if (dirty) {
    status = `Unsaved changes. Saving creates v${tool.current_version + 1}.`;
  } else {
    status = `Current version v${tool.current_version}.`;
  }
  const callable = `${toolsetId}__${toolId || 'tool_id'}`;
  const toolsetDisabled = tool?.disabled_reason === 'toolset_disabled';

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
              Back to tools
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
            {tool ? (
              <TextField
                size="small"
                label="Version comment"
                placeholder="What changed"
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
              gridTemplateColumns: {
                xs: 'minmax(0, 1fr)',
                lg: 'minmax(0, 1fr) 380px',
              },
              maxWidth: 1440,
              px: { xs: 2, md: 3 },
              py: 3,
            }}
          >
            <Box
              sx={{
                alignContent: 'start',
                display: 'grid',
                gap: 5,
                minWidth: 0,
              }}
            >
              {error ? <Alert severity="error">{error}</Alert> : null}

              <Section
                title="How agents see it"
                description="Chat, workflows and MCP clients call the tool by this name, choose it by its description, and fill in these arguments."
              >
                <Box sx={{ ...panelSx, display: 'grid', gap: 1.5, p: 2.5 }}>
                  <Signature callable={callable} params={draft.params} />
                  <Typography
                    variant="body2"
                    color={
                      draft.description.trim()
                        ? 'text.primary'
                        : 'text.secondary'
                    }
                    sx={{ maxWidth: '75ch', whiteSpace: 'pre-wrap' }}
                  >
                    {draft.description.trim() ||
                      'No description yet. Agents pick tools by what this says, so say what it returns and when to reach for it.'}
                  </Typography>
                </Box>
              </Section>

              <Section title="Identity">
                <Box
                  sx={{
                    display: 'grid',
                    gap: 2,
                    gridTemplateColumns: {
                      xs: 'minmax(0, 1fr)',
                      md: 'repeat(2, minmax(0, 1fr))',
                    },
                  }}
                >
                  <TextField
                    label="Name"
                    required
                    value={draft.name}
                    onChange={(e) => update({ name: e.target.value })}
                    placeholder="Recent critical CVEs"
                    slotProps={{ inputLabel: { shrink: true } }}
                  />
                  <TextField
                    label="ID"
                    required={!tool}
                    disabled={!!tool}
                    value={tool ? tool.tool_id : draft.tool_id}
                    onChange={(e) => update({ tool_id: e.target.value })}
                    error={toolIdError !== null}
                    helperText={
                      tool
                        ? 'Fixed once saved.'
                        : (toolIdError ??
                          `lower_snake_case · ${toolId.length}/${MAX_SLUG_LEN} · fixed once saved`)
                    }
                    sx={monoInputSx}
                    slotProps={{
                      inputLabel: { shrink: true },
                      htmlInput: { spellCheck: false },
                      input: {
                        startAdornment: (
                          <InputAdornment position="start" sx={{ mr: 0 }}>
                            <Box
                              component="span"
                              sx={{
                                color: 'text.secondary',
                                fontFamily: fontFamilyMono,
                                fontSize: 14,
                              }}
                            >
                              {toolsetId}__
                            </Box>
                          </InputAdornment>
                        ),
                      },
                    }}
                  />
                  <TextField
                    label="Description"
                    multiline
                    minRows={2}
                    value={draft.description}
                    onChange={(e) => update({ description: e.target.value })}
                    placeholder="Lists CVEs published in the last N days at or above a severity. Use it to answer “what’s new and serious?”"
                    sx={{ gridColumn: { md: '1 / -1' } }}
                    slotProps={{ inputLabel: { shrink: true } }}
                  />
                </Box>
              </Section>

              <Section
                title="Query"
                description="Read-only Cypher. Arguments arrive as $parameters; writes are rejected when you save."
              >
                <Box
                  sx={{
                    ...panelSx,
                    overflow: 'hidden',
                    transition: 'border-color 150ms ease-out',
                    '&:focus-within': { borderColor: 'primary.main' },
                  }}
                >
                  <InputBase
                    multiline
                    fullWidth
                    minRows={8}
                    value={draft.cypher}
                    onChange={(e) => update({ cypher: e.target.value })}
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                        e.preventDefault();
                        draftRun.start();
                      }
                    }}
                    placeholder={
                      'MATCH (c:CVE)\nWHERE c.severity = $severity\nRETURN c.id, c.published\nLIMIT $limit'
                    }
                    inputProps={{
                      'aria-label': 'Cypher query',
                      spellCheck: false,
                    }}
                    sx={{
                      alignItems: 'flex-start',
                      fontFamily: fontFamilyMono,
                      fontSize: 14,
                      lineHeight: 1.65,
                      px: 2,
                      py: 1.5,
                      '& textarea::placeholder': {
                        color: 'text.secondary',
                        opacity: 1,
                      },
                    }}
                  />
                  <Box
                    data-testid="query-parameters"
                    sx={{
                      alignItems: 'center',
                      bgcolor: 'action.hover',
                      borderTop: 1,
                      borderColor: 'divider',
                      columnGap: 2,
                      display: 'flex',
                      flexWrap: 'wrap',
                      minHeight: 44,
                      px: 2,
                      py: 0.75,
                      rowGap: 0.5,
                    }}
                  >
                    {referenced.length === 0 ? (
                      <Typography color="text.secondary" variant="body2">
                        No $parameters in the query.
                      </Typography>
                    ) : (
                      referenced.map((name) => {
                        const known = declared.has(name);
                        return (
                          <Box
                            key={name}
                            sx={{
                              alignItems: 'center',
                              display: 'inline-flex',
                              gap: 0.5,
                            }}
                          >
                            {known ? (
                              <CheckIcon
                                sx={{ color: 'success.main', fontSize: 16 }}
                                titleAccess="Declared"
                              />
                            ) : (
                              <WarningAmberIcon
                                sx={{ color: 'warning.main', fontSize: 16 }}
                                titleAccess="Not declared"
                              />
                            )}
                            <Box
                              component="span"
                              sx={{
                                color: known ? 'text.primary' : 'warning.main',
                                fontFamily: fontFamilyMono,
                                fontSize: 13,
                              }}
                            >
                              {`$${name}`}
                            </Box>
                            {known ? null : (
                              <Tooltip title={`Add $${name} as a parameter`}>
                                <Button
                                  size="small"
                                  onClick={() => addParam(name)}
                                  sx={{ minWidth: 0, px: 0.75, py: 0 }}
                                  aria-label={`Declare $${name}`}
                                >
                                  Declare
                                </Button>
                              </Tooltip>
                            )}
                          </Box>
                        );
                      })
                    )}
                  </Box>
                </Box>
              </Section>

              <Section
                title="Parameters"
                description={
                  undeclared.length > 0
                    ? `The query uses ${undeclared.map((n) => `$${n}`).join(', ')} without declaring ${undeclared.length === 1 ? 'it' : 'them'}, so an agent cannot supply ${undeclared.length === 1 ? 'it' : 'them'}.`
                    : 'Each parameter becomes an argument an agent can pass. Optional ones fall back to their default, or null.'
                }
                action={
                  draft.params.length > 0 ? (
                    <Button startIcon={<AddIcon />} onClick={() => addParam()}>
                      Add parameter
                    </Button>
                  ) : null
                }
              >
                {draft.params.length === 0 ? (
                  <Box
                    sx={{
                      ...panelSx,
                      alignItems: 'center',
                      borderStyle: 'dashed',
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 2,
                      justifyContent: 'space-between',
                      px: 2,
                      py: 2,
                    }}
                  >
                    <Typography color="text.secondary" variant="body2">
                      None. Agents call this tool with no arguments.
                    </Typography>
                    <Button startIcon={<AddIcon />} onClick={() => addParam()}>
                      Add parameter
                    </Button>
                  </Box>
                ) : (
                  <Box
                    component="ol"
                    sx={{ ...panelSx, listStyle: 'none', m: 0, p: 0 }}
                  >
                    {draft.params.map((p, index) => (
                      <ParamRow
                        key={p.key}
                        param={p}
                        nameError={paramErrors[index]}
                        referenced={referenced.includes(p.name.trim())}
                        onChange={(patch) => updateParam(p.key, patch)}
                        onRemove={() => removeParam(p.key)}
                      />
                    ))}
                  </Box>
                )}
              </Section>
            </Box>

            <Box
              component="aside"
              sx={{
                alignSelf: 'start',
                display: 'grid',
                gap: 3,
                minWidth: 0,
                position: { lg: 'sticky' },
                top: { lg: 24 },
              }}
            >
              <TryIt
                cypher={draft.cypher}
                canRun={canRun}
                draftRun={draftRun}
              />
              <Box sx={{ ...panelSx, display: 'grid', gap: 1, p: 2 }}>
                <Typography variant="h6" component="h2">
                  Availability
                </Typography>
                <FormControlLabel
                  sx={{ alignItems: 'flex-start', m: 0 }}
                  control={
                    <Switch
                      checked={draft.enabled}
                      onChange={(e) => update({ enabled: e.target.checked })}
                    />
                  }
                  label={
                    <Box sx={{ pt: 0.75 }}>
                      <Typography variant="body2">Enabled</Typography>
                      <Typography color="text.secondary" variant="caption">
                        {toolsetDisabled
                          ? 'Its toolset is disabled, so agents cannot call it until the toolset is enabled.'
                          : 'Agents and MCP clients can call it.'}
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
        onConfirm={() => navigate(listPath)}
      >
        Your edits to <strong>{draft.name.trim() || 'this tool'}</strong> have
        not been saved. Leaving discards them.
      </ConfirmDeleteDialog>
    </>
  );
}

export default function ToolEditor() {
  const { toolsetId = '', toolId } = useParams<{
    toolsetId: string;
    toolId: string;
  }>();
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const { tools, loading, error } = useToolsList(toolsetId || null);
  const tool = toolId
    ? (tools.find((candidate) => candidate.tool_id === toolId) ?? null)
    : null;
  const ready = !permissionsLoading && (!toolId || !loading);
  let body: React.ReactNode = null;
  if (ready && isReadOnlyToolset(toolsetId)) {
    body = (
      <Typography>Built-in and external MCP tools cannot be edited.</Typography>
    );
  } else if (ready && !hasPermission('tools:write')) {
    body = <Typography>You do not have permission to edit tools.</Typography>;
  } else if (ready && toolId && !tool && !error) {
    body = <Typography>This tool no longer exists.</Typography>;
  }
  if (ready && !body && !(toolId && error)) {
    return (
      <Editor key={tool?.tool_id ?? 'new'} toolsetId={toolsetId} tool={tool} />
    );
  }
  return (
    <Box sx={{ p: { xs: 1.5, sm: 2 } }}>
      <PageTitle>Tools | Seizu</PageTitle>
      <ListViewState
        loading={!ready}
        error={toolId ? error : null}
        errorMessage="Failed to load the tool"
      >
        {body}
      </ListViewState>
    </Box>
  );
}
