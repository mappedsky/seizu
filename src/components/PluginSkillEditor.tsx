import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import DeleteIcon from '@mui/icons-material/Delete';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlineOutlined';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import FieldWithHelp from 'src/components/FieldWithHelp';
import MarkdownEditor from 'src/components/MarkdownEditor';
import type { PluginFileInfo } from 'src/hooks/usePluginsApi';
import type { ToolCatalogItem, ToolParamDef } from 'src/hooks/useToolsetsApi';
import {
  PluginSkillExtension,
  SkillDocument,
  validateSkillAuthoring,
} from 'src/pluginAuthoring';

type SupportingDirectory = 'references' | 'scripts' | 'assets';

interface ParamFormState {
  // Stable across renames and reorders, so a row keeps its React identity while
  // its name field is still empty or being typed into.
  key: string;
  name: string;
  type: ToolParamDef['type'];
  description: string;
  required: boolean;
  defaultText: string;
}

let parameterKeySeq = 0;

function newParameter(): ParamFormState {
  parameterKeySeq += 1;
  return {
    key: `parameter-${parameterKeySeq}`,
    name: '',
    type: 'string',
    description: '',
    required: true,
    defaultText: '',
  };
}

function toParamForm(parameter: ToolParamDef): ParamFormState {
  parameterKeySeq += 1;
  return {
    key: `parameter-${parameterKeySeq}`,
    name: parameter.name,
    type: parameter.type,
    description: parameter.description ?? '',
    required: parameter.required,
    defaultText:
      parameter.default === null || parameter.default === undefined
        ? ''
        : String(parameter.default),
  };
}

function fromParamForm(parameter: ParamFormState): ToolParamDef {
  let defaultValue: unknown = null;
  if (parameter.defaultText.trim()) {
    if (parameter.type === 'integer') {
      defaultValue = Number.parseInt(parameter.defaultText, 10);
    } else if (parameter.type === 'float') {
      defaultValue = Number.parseFloat(parameter.defaultText);
    } else if (parameter.type === 'boolean') {
      defaultValue = parameter.defaultText.toLowerCase() === 'true';
    } else {
      defaultValue = parameter.defaultText;
    }
  }
  return {
    name: parameter.name.trim(),
    type: parameter.type,
    description: parameter.description.trim(),
    required: parameter.required,
    default: defaultValue,
  };
}

function StringListEditor({
  label,
  values,
  onChange,
  tooltip,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  tooltip: string;
}) {
  return (
    <FieldWithHelp label={label} tooltip={tooltip}>
      <Autocomplete
        multiple
        freeSolo
        options={[]}
        value={values}
        onChange={(_event, next) =>
          onChange([
            ...new Set(next.map((value) => value.trim()).filter(Boolean)),
          ])
        }
        renderInput={(params) => (
          <TextField {...params} label={label} size="small" />
        )}
        sx={{ flex: 1, minWidth: 0 }}
      />
    </FieldWithHelp>
  );
}

function AllowedToolsDialog({
  open,
  tools,
  selected,
  onClose,
  onSave,
}: {
  open: boolean;
  tools: ToolCatalogItem[];
  selected: string[];
  onClose: () => void;
  onSave: (tools: string[]) => void;
}) {
  const [selection, setSelection] = useState(selected);
  const groups = useMemo(() => {
    const grouped = new Map<
      string,
      { label: string; tools: ToolCatalogItem[] }
    >();
    for (const tool of tools) {
      const key = tool.toolset_id || tool.toolset_name;
      const group = grouped.get(key) ?? {
        label: tool.toolset_name,
        tools: [],
      };
      group.tools.push(tool);
      grouped.set(key, group);
    }
    return [...grouped.entries()]
      .map(([key, group]) => ({
        key,
        ...group,
        tools: group.tools.sort((left, right) =>
          left.mcp_name.localeCompare(right.mcp_name),
        ),
      }))
      .sort((left, right) => left.label.localeCompare(right.label));
  }, [tools]);

  const toggle = (name: string) => {
    setSelection((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name].sort(),
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Allowed tools</DialogTitle>
      <DialogContent dividers>
        {groups.length === 0 ? (
          <Typography color="text.secondary">
            No tools are available from enabled toolsets.
          </Typography>
        ) : (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: {
                xs: '1fr',
                md: 'repeat(2, minmax(0, 1fr))',
              },
              gap: 2,
            }}
          >
            {groups.map((group) => (
              <Paper key={group.key} variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.5 }}>
                  {group.label}
                </Typography>
                <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                  {group.tools.map((tool) => (
                    <FormControlLabel
                      key={tool.mcp_name}
                      control={
                        <Checkbox
                          checked={selection.includes(tool.mcp_name)}
                          onChange={() => toggle(tool.mcp_name)}
                        />
                      }
                      label={
                        <Box sx={{ minWidth: 0 }}>
                          <Typography variant="body2">
                            {tool.name || tool.mcp_name}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontFamily: 'monospace' }}
                          >
                            {tool.mcp_name}
                          </Typography>
                        </Box>
                      }
                    />
                  ))}
                </Box>
              </Paper>
            ))}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => {
            onSave(selection);
            onClose();
          }}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function SupportingFileDialog({
  open,
  onClose,
  onUpload,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onUpload: (directory: SupportingDirectory, file: File) => Promise<void>;
  onCreate: (
    directory: SupportingDirectory,
    filename: string,
    content: string,
  ) => Promise<void>;
}) {
  const [directory, setDirectory] = useState<SupportingDirectory>('references');
  const [mode, setMode] = useState<'upload' | 'text'>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [filename, setFilename] = useState('');
  const [content, setContent] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (mode === 'upload' && !file) {
      setError('Choose a file to upload.');
      return;
    }
    const name = filename.trim();
    if (
      mode === 'text' &&
      (!name || name.includes('/') || name === '.' || name === '..')
    ) {
      setError('Filename must be a single file name.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (mode === 'upload') await onUpload(directory, file as File);
      else await onCreate(directory, name, content);
      onClose();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add supporting file</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          <FormControl fullWidth size="small">
            <InputLabel id="supporting-directory-label">Directory</InputLabel>
            <Select
              id="supporting-directory"
              labelId="supporting-directory-label"
              label="Directory"
              value={directory}
              onChange={(event) =>
                setDirectory(event.target.value as SupportingDirectory)
              }
            >
              <MenuItem value="references">references</MenuItem>
              <MenuItem value="scripts">scripts</MenuItem>
              <MenuItem value="assets">assets</MenuItem>
            </Select>
          </FormControl>
          <FormControl fullWidth size="small">
            <InputLabel id="supporting-source-label">Source</InputLabel>
            <Select
              id="supporting-source"
              labelId="supporting-source-label"
              label="Source"
              value={mode}
              onChange={(event) =>
                setMode(event.target.value as 'upload' | 'text')
              }
            >
              <MenuItem value="upload">Upload a file</MenuItem>
              <MenuItem value="text">Create a text file</MenuItem>
            </Select>
          </FormControl>
          {mode === 'upload' ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Button
                component="label"
                variant="outlined"
                startIcon={<UploadFileIcon />}
              >
                Choose file
                <input
                  hidden
                  type="file"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </Button>
              <Typography variant="body2" color="text.secondary" noWrap>
                {file?.name ?? 'No file selected'}
              </Typography>
            </Box>
          ) : (
            <>
              <TextField
                label="Filename"
                value={filename}
                onChange={(event) => setFilename(event.target.value)}
                required
                fullWidth
                size="small"
              />
              <TextField
                label="Contents"
                value={content}
                onChange={(event) => setContent(event.target.value)}
                multiline
                minRows={10}
                fullWidth
                slotProps={{
                  htmlInput: {
                    spellCheck: false,
                    style: { fontFamily: 'monospace' },
                  },
                }}
              />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? <ConstellationSpinner size={20} /> : 'Add file'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function PluginSkillEditor({
  document,
  extension,
  catalog,
  supportingFiles,
  onChange,
  onUpload,
  onCreateTextFile,
  onDeleteFile,
  onDeleteSkill,
}: {
  document: SkillDocument;
  extension: PluginSkillExtension;
  catalog: ToolCatalogItem[];
  supportingFiles: PluginFileInfo[];
  /** Called on every edit: this editor stages into the parent, never the server. */
  onChange: (document: SkillDocument, extension: PluginSkillExtension) => void;
  onUpload: (directory: SupportingDirectory, file: File) => Promise<void>;
  onCreateTextFile: (
    directory: SupportingDirectory,
    filename: string,
    content: string,
  ) => Promise<void>;
  onDeleteFile: (file: PluginFileInfo) => Promise<void>;
  onDeleteSkill: () => Promise<void>;
}) {
  const [description, setDescription] = useState(document.description);
  const [body, setBody] = useState(document.body);
  const [allowedTools, setAllowedTools] = useState(document.allowedTools);
  const [skillId, setSkillId] = useState(
    extension.skillId ?? document.portableName.replaceAll('-', '_'),
  );
  const [title, setTitle] = useState(
    extension.title ??
      document.portableName
        .split('-')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' '),
  );
  const [enabled, setEnabled] = useState(extension.enabled ?? true);
  const [triggers, setTriggers] = useState(extension.triggers ?? []);
  const [aliases, setAliases] = useState(extension.aliases ?? []);
  const [parameters, setParameters] = useState<ParamFormState[]>(
    (extension.parameters ?? []).map(toParamForm),
  );
  const [newFileOpen, setNewFileOpen] = useState(false);
  const [allowedToolsOpen, setAllowedToolsOpen] = useState(false);

  const availableTools = useMemo(() => {
    const values = new Map(catalog.map((tool) => [tool.mcp_name, tool]));
    for (const name of allowedTools) {
      if (!values.has(name)) {
        values.set(name, {
          mcp_name: name,
          name,
          tool_id: name,
          toolset_id: '',
          toolset_name: 'Package declaration',
          description: '',
          input_schema: {},
          enabled: true,
        } as ToolCatalogItem);
      }
    }
    return [...values.values()];
  }, [allowedTools, catalog]);

  const updateParameter = <K extends keyof ParamFormState>(
    index: number,
    key: K,
    value: ParamFormState[K],
  ) =>
    setParameters((items) =>
      items.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [key]: value } : item,
      ),
    );

  const stagedDocument: SkillDocument = {
    ...document,
    description,
    body,
    allowedTools,
  };
  const typedParameters = parameters.map(fromParamForm);
  const problem = validateSkillAuthoring(
    { ...stagedDocument, description: description.trim() },
    skillId.trim(),
    typedParameters,
  );

  // Every edit stages straight into the parent's in-memory package; nothing
  // reaches the server until Publish. The ref keeps the mount-time push (which
  // is identical to what the parent already holds) from being reported as an
  // edit.
  const stagedRef = useRef(onChange);
  stagedRef.current = onChange;
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    stagedRef.current(
      { ...document, description: description.trim(), body, allowedTools },
      {
        skillId: skillId.trim(),
        title: title.trim() || undefined,
        enabled,
        triggers,
        aliases,
        parameters: typedParameters,
      },
    );
  }, [
    description,
    body,
    allowedTools,
    skillId,
    title,
    enabled,
    triggers,
    aliases,
    JSON.stringify(typedParameters),
  ]);

  const handleBodyChange = useCallback(
    (value: string | undefined) => setBody(value ?? ''),
    [],
  );

  return (
    <Stack spacing={2.5}>
      {problem && <Alert severity="error">{problem}</Alert>}
      <Box>
        <Typography variant="h2" sx={{ mb: 0.5 }}>
          {title || document.portableName}
        </Typography>
        <Typography color="text.secondary" sx={{ fontFamily: 'monospace' }}>
          skills/{document.portableName}/SKILL.md
        </Typography>
      </Box>

      {/* A grid, not a row Stack: the help affordance makes two of these three
          controls wider than their input, and flex sizing then starved them. */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: {
            xs: '1fr',
            md: 'repeat(3, minmax(0, 1fr))',
          },
        }}
      >
        <FieldWithHelp
          label="Portable name"
          tooltip="This is both the skill directory and front matter name. It cannot be changed after creation."
        >
          <TextField
            label="Portable name"
            value={document.portableName}
            disabled
            fullWidth
          />
        </FieldWithHelp>
        <FieldWithHelp
          label="Skill ID"
          tooltip="This appears after the plugin namespace in the Seizu skill name."
        >
          <TextField
            label="Skill ID"
            value={skillId}
            onChange={(event) => setSkillId(event.target.value)}
            required
            fullWidth
          />
        </FieldWithHelp>
        <TextField
          label="Display title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          fullWidth
        />
      </Box>
      <FieldWithHelp
        label="Description"
        tooltip="This description is written to the SKILL.md front matter and used during skill discovery."
      >
        <TextField
          label="Description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          multiline
          minRows={2}
          required
          fullWidth
        />
      </FieldWithHelp>
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Instructions
        </Typography>
        <MarkdownEditor
          value={body}
          onChange={handleBodyChange}
          sourceLabel="SKILL.md instructions"
          availableVariables={parameters
            .filter((parameter) => parameter.name.trim())
            .map((parameter) => ({ name: parameter.name.trim() }))}
        />
      </Box>

      <FieldWithHelp
        label="Allowed tools"
        tooltip="These dependencies are written to the standard allowed-tools front matter field. They disclose tools but do not grant permissions."
      >
        <Box
          sx={{
            alignItems: 'center',
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            display: 'flex',
            gap: 1,
            justifyContent: 'space-between',
            minHeight: 56,
            px: 1.5,
            py: 1,
          }}
        >
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {allowedTools.length ? (
              allowedTools.map((name) => (
                <Chip key={name} label={name} size="small" variant="outlined" />
              ))
            ) : (
              <Typography variant="body2" color="text.secondary">
                No tools selected
              </Typography>
            )}
          </Box>
          <Button
            variant="outlined"
            size="small"
            onClick={() => setAllowedToolsOpen(true)}
          >
            Choose tools
          </Button>
        </Box>
      </FieldWithHelp>
      <FormControlLabel
        control={
          <Switch
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
        }
        label="Enabled"
      />
      <StringListEditor
        label="Triggers"
        values={triggers}
        onChange={setTriggers}
        tooltip="Trigger phrases help the agent recognize when this skill applies. Press Enter after each phrase."
      />
      <StringListEditor
        label="Aliases"
        values={aliases}
        onChange={setAliases}
        tooltip="Aliases preserve alternate or previous names for this skill. Press Enter after each alias."
      />

      <Divider />
      <Box>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1 }}>
          <Typography variant="subtitle2">Inputs</Typography>
          <Tooltip
            title="Values a caller passes for one invocation. They are rendered into an Inputs block after the instructions, so the instructions themselves stay the same for every run — refer to an input by name rather than substituting it."
            placement="top"
            arrow
            describeChild
          >
            <IconButton aria-label="Help for inputs" size="small">
              <HelpOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <IconButton
            size="small"
            aria-label="Add input"
            onClick={() => setParameters((items) => [...items, newParameter()])}
          >
            <AddCircleOutlineIcon fontSize="small" />
          </IconButton>
        </Stack>
        {parameters.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            No inputs.
          </Typography>
        )}
        {parameters.map((parameter, index) => (
          <Box
            key={parameter.key}
            sx={{
              border: 1,
              borderColor: 'divider',
              borderRadius: 1,
              p: 1.5,
              mb: 1.5,
            }}
          >
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: {
                  xs: '1fr',
                  lg: 'minmax(180px, 2fr) minmax(110px, 1fr) minmax(140px, 1fr) auto auto',
                },
                gap: 1.5,
                alignItems: 'start',
              }}
            >
              <TextField
                label="Name"
                value={parameter.name}
                onChange={(event) =>
                  updateParameter(index, 'name', event.target.value)
                }
                size="small"
                required
              />
              <FormControl size="small">
                <InputLabel>Type</InputLabel>
                <Select
                  label="Type"
                  value={parameter.type}
                  onChange={(event) =>
                    updateParameter(
                      index,
                      'type',
                      event.target.value as ToolParamDef['type'],
                    )
                  }
                >
                  <MenuItem value="string">string</MenuItem>
                  <MenuItem value="integer">integer</MenuItem>
                  <MenuItem value="float">float</MenuItem>
                  <MenuItem value="boolean">boolean</MenuItem>
                </Select>
              </FormControl>
              <TextField
                label="Default"
                value={parameter.defaultText}
                onChange={(event) =>
                  updateParameter(index, 'defaultText', event.target.value)
                }
                size="small"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={parameter.required}
                    onChange={(event) =>
                      updateParameter(index, 'required', event.target.checked)
                    }
                    size="small"
                  />
                }
                label="Required"
              />
              <IconButton
                size="small"
                aria-label="Remove input"
                onClick={() =>
                  setParameters((items) =>
                    items.filter((_, itemIndex) => itemIndex !== index),
                  )
                }
              >
                <RemoveCircleOutlineIcon fontSize="small" />
              </IconButton>
            </Box>
            <TextField
              label="Description"
              value={parameter.description}
              onChange={(event) =>
                updateParameter(index, 'description', event.target.value)
              }
              size="small"
              fullWidth
              sx={{ mt: 1.5 }}
            />
          </Box>
        ))}
      </Box>

      <Divider />
      <Box>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <Typography variant="subtitle2">Supporting files</Typography>
          <Tooltip
            title="Add a reference, executable script, or asset beneath this skill's package directory."
            placement="top"
            arrow
            describeChild
          >
            <IconButton aria-label="Help for supporting files" size="small">
              <HelpOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Button
            variant="outlined"
            size="small"
            startIcon={<AddCircleOutlineIcon />}
            onClick={() => setNewFileOpen(true)}
          >
            Add file
          </Button>
        </Stack>
        <Stack spacing={0.5} sx={{ mt: 1.5 }}>
          {supportingFiles.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No supporting files.
            </Typography>
          )}
          {supportingFiles.map((file) => (
            <Stack
              key={file.path}
              direction="row"
              spacing={1}
              sx={{ alignItems: 'center', py: 0.5 }}
            >
              <Typography
                variant="body2"
                sx={{ fontFamily: 'monospace', flex: 1 }}
              >
                {file.path.split('/').slice(-2).join('/')}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {file.size} bytes
              </Typography>
              <IconButton
                size="small"
                color="error"
                aria-label={`Delete ${file.path}`}
                onClick={() => void onDeleteFile(file)}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Stack>
          ))}
        </Stack>
      </Box>

      <Divider />
      <Stack direction="row" sx={{ justifyContent: 'flex-start' }}>
        <Button
          color="error"
          startIcon={<DeleteIcon />}
          onClick={() => void onDeleteSkill()}
        >
          Remove skill
        </Button>
      </Stack>

      <SupportingFileDialog
        key={newFileOpen ? 'open' : 'closed'}
        open={newFileOpen}
        onClose={() => setNewFileOpen(false)}
        onUpload={onUpload}
        onCreate={onCreateTextFile}
      />
      {allowedToolsOpen && (
        <AllowedToolsDialog
          open
          tools={availableTools}
          selected={allowedTools}
          onClose={() => setAllowedToolsOpen(false)}
          onSave={setAllowedTools}
        />
      )}
    </Stack>
  );
}
