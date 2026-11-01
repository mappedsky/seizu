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
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import BuildIcon from '@mui/icons-material/Build';
import CancelIcon from '@mui/icons-material/Cancel';
import DeleteIcon from '@mui/icons-material/Delete';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import AllowedToolsDialog from 'src/components/AllowedToolsDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import {
  EditorSection,
  IdentityFact,
  IdentityFacts,
} from 'src/components/EditorSection';
import FieldWithHelp from 'src/components/FieldWithHelp';
import MarkdownEditor from 'src/components/MarkdownEditor';
import type { PluginFileInfo } from 'src/hooks/usePluginsApi';
import type { ToolCatalogItem, ToolParamDef } from 'src/hooks/useToolsetsApi';
import {
  PluginSkillExtension,
  SkillDocument,
  declaredToolName,
  isExternalDeclaration,
  toolDeclaration,
  validateSkillAuthoring,
} from 'src/pluginAuthoring';
import { fontFamilyMono } from 'src/theme/typography';

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

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function EmptyLine({ children }: { children: string }) {
  return (
    <Typography variant="body2" color="text.secondary">
      {children}
    </Typography>
  );
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
                    style: { fontFamily: fontFamilyMono },
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

function defaultTitle(portableName: string): string {
  return portableName
    .split('-')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * The skill as this editor stages it before anyone edits it: defaults filled
 * in, text trimmed, and tool declarations in the spelling the server
 * resolves. Idempotent, so comparing two skills through it tells an edit from
 * a difference the editor introduced by opening the skill.
 */
export function normalizeSkill(
  document: SkillDocument,
  extension: PluginSkillExtension,
): { document: SkillDocument; extension: PluginSkillExtension } {
  return {
    document: {
      ...document,
      description: document.description.trim(),
      body: document.body,
      allowedTools: document.allowedTools.map(toolDeclaration),
    },
    extension: {
      // Not written: the id derives from the portable name, and a manifest
      // that repeats it is flagged as redundant on publish (AGT-040).
      title:
        (extension.title ?? defaultTitle(document.portableName)).trim() ||
        undefined,
      triggers: extension.triggers ?? [],
      aliases: extension.aliases ?? [],
      parameters: (extension.parameters ?? [])
        .map(toParamForm)
        .map(fromParamForm),
    },
  };
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
  qualifiedName,
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
  /** The `plugin__skill` name agents call this skill by, when known. */
  qualifiedName?: string;
}) {
  const [description, setDescription] = useState(document.description);
  const [body, setBody] = useState(document.body);
  // A declaration written before the mcp__<server>__ form was required still
  // names a real tool. Normalizing on load checks its box, and the next edit
  // to the skill publishes it in the spelling the server resolves.
  const [allowedTools, setAllowedTools] = useState(() =>
    document.allowedTools.map(toolDeclaration),
  );
  // Derived, never authored: the portable name is the skill's identity.
  const skillId = document.portableName.replaceAll('-', '_');
  const [title, setTitle] = useState(
    () => extension.title ?? defaultTitle(document.portableName),
  );
  const [triggers, setTriggers] = useState(extension.triggers ?? []);
  const [aliases, setAliases] = useState(extension.aliases ?? []);
  const [parameters, setParameters] = useState<ParamFormState[]>(
    (extension.parameters ?? []).map(toParamForm),
  );
  const [newFileOpen, setNewFileOpen] = useState(false);
  const [allowedToolsOpen, setAllowedToolsOpen] = useState(false);

  const availableTools = useMemo(() => {
    const values = new Map(
      catalog.map((tool) => [toolDeclaration(tool.mcp_name), tool]),
    );
    for (const entry of allowedTools) {
      if (!values.has(entry)) {
        const name = declaredToolName(entry);
        values.set(entry, {
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

  // A dependency on another MCP server resolves only when mcp.json declares
  // it, and the editor has no view of that file -- so say what the package
  // needs rather than letting the skill go quietly unavailable.
  const externalServers = useMemo(
    () => [
      ...new Set(
        allowedTools
          .filter(isExternalDeclaration)
          .map((entry) => entry.slice('mcp__'.length).split('__')[0]),
      ),
    ],
    [allowedTools],
  );

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
  // reaches the server until Publish. Only a state that differs from the last
  // one staged is pushed, starting from the skill as it opened, so opening a
  // skill -- or StrictMode re-running this effect -- is never an edit.
  const staged = {
    document: { ...stagedDocument, description: description.trim() },
    extension: {
      title: title.trim() || undefined,
      triggers,
      aliases,
      parameters: typedParameters,
    },
  };
  const stagedKey = JSON.stringify(staged);
  const stagedRef = useRef(onChange);
  stagedRef.current = onChange;
  const pushedRef = useRef<string | null>(null);
  const [openedKey] = useState(() =>
    JSON.stringify(normalizeSkill(document, extension)),
  );
  useEffect(() => {
    if (stagedKey === (pushedRef.current ?? openedKey)) return;
    pushedRef.current = stagedKey;
    const next = JSON.parse(stagedKey) as typeof staged;
    stagedRef.current(next.document, next.extension);
  }, [openedKey, stagedKey]);

  const handleBodyChange = useCallback(
    (value: string | undefined) => setBody(value ?? ''),
    [],
  );

  return (
    <Stack spacing={{ xs: 3, md: 4 }}>
      <Box>
        <Typography
          variant="h2"
          sx={{ fontSize: { xs: 24, md: 29 }, overflowWrap: 'anywhere' }}
        >
          {title || document.portableName}
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontFamily: fontFamilyMono, mt: 0.5, overflowWrap: 'anywhere' }}
        >
          skills/{document.portableName}/SKILL.md
        </Typography>
        {/* Derived, never authored: the portable name is the skill's
            identity and fixes the others, so none of these is an input. */}
        <IdentityFacts>
          <IdentityFact label="Portable name" value={document.portableName} />
          <IdentityFact label="Skill ID" value={skillId} />
          {qualifiedName && (
            <IdentityFact label="Tool name" value={qualifiedName} />
          )}
        </IdentityFacts>
      </Box>
      {problem && <Alert severity="error">{problem}</Alert>}

      <EditorSection
        title="Discovery"
        description="How an agent finds this skill and decides it applies. The description is written to the SKILL.md front matter."
      >
        <Stack spacing={2}>
          <TextField
            label="Display title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            fullWidth
          />
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
        </Stack>
      </EditorSection>

      <EditorSection
        title="Instructions"
        description="The SKILL.md body the agent follows once the skill is loaded."
      >
        <MarkdownEditor
          value={body}
          onChange={handleBodyChange}
          sourceLabel="SKILL.md instructions"
          availableVariables={parameters
            .filter((parameter) => parameter.name.trim())
            .map((parameter) => ({ name: parameter.name.trim() }))}
        />
      </EditorSection>

      <EditorSection
        title="Allowed tools"
        description="Dependencies written to the allowed-tools front matter field. They disclose tools to the agent; they do not grant permissions."
        action={
          <Button
            variant="outlined"
            size="small"
            startIcon={<BuildIcon fontSize="small" />}
            onClick={() => setAllowedToolsOpen(true)}
            sx={{ whiteSpace: 'nowrap' }}
          >
            Choose tools
          </Button>
        }
      >
        {allowedTools.length ? (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {allowedTools.map((name) => (
              <Chip
                key={name}
                label={name}
                size="small"
                variant="outlined"
                onDelete={() =>
                  setAllowedTools((current) =>
                    current.filter((item) => item !== name),
                  )
                }
                deleteIcon={<CancelIcon titleAccess={`Remove ${name}`} />}
                sx={{ fontFamily: fontFamilyMono, maxWidth: '100%' }}
              />
            ))}
          </Box>
        ) : (
          <EmptyLine>No tools selected</EmptyLine>
        )}
        {externalServers.length > 0 && (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {externalServers.length === 1
              ? `${externalServers[0]} is an external MCP server. This package needs an mcp.json entry naming it, or the skill stays unavailable.`
              : `${externalServers.join(', ')} are external MCP servers. This package needs an mcp.json entry naming each of them, or the skill stays unavailable.`}
          </Alert>
        )}
      </EditorSection>

      <EditorSection
        title="Inputs"
        description="Values a caller passes for one invocation. They are rendered into an Inputs block after the instructions, so refer to an input by name rather than substituting it."
        action={
          <Button
            variant="outlined"
            size="small"
            startIcon={<AddCircleOutlineIcon />}
            onClick={() => setParameters((items) => [...items, newParameter()])}
          >
            Add input
          </Button>
        }
      >
        {parameters.length === 0 && <EmptyLine>No inputs.</EmptyLine>}
        <Stack spacing={1.5}>
          {parameters.map((parameter, index) => (
            <Box
              key={parameter.key}
              sx={{
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                p: { xs: 1.5, md: 2 },
              }}
            >
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: {
                    xs: '1fr 1fr',
                    lg: 'minmax(180px, 2fr) minmax(110px, 1fr) minmax(140px, 1fr) auto auto',
                  },
                  gap: 1.5,
                  alignItems: 'center',
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
                  sx={{ gridColumn: { xs: '1 / -1', lg: 'auto' } }}
                  slotProps={{
                    htmlInput: { style: { fontFamily: fontFamilyMono } },
                  }}
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
                <Box sx={{ justifySelf: 'end' }}>
                  <Tooltip title="Remove input">
                    <IconButton
                      aria-label="Remove input"
                      onClick={() =>
                        setParameters((items) =>
                          items.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                    >
                      <RemoveCircleOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
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
        </Stack>
      </EditorSection>

      <EditorSection
        title="Supporting files"
        description="References, executable scripts and assets beneath this skill's package directory."
        action={
          <Button
            variant="outlined"
            size="small"
            startIcon={<AddCircleOutlineIcon />}
            onClick={() => setNewFileOpen(true)}
          >
            Add file
          </Button>
        }
      >
        {supportingFiles.length === 0 ? (
          <EmptyLine>No supporting files.</EmptyLine>
        ) : (
          <Box
            component="ul"
            sx={{
              border: 1,
              borderColor: 'divider',
              borderRadius: 1,
              listStyle: 'none',
              m: 0,
              p: 0,
            }}
          >
            {supportingFiles.map((file, index) => (
              <Box
                component="li"
                key={file.path}
                sx={{
                  alignItems: 'center',
                  borderTop: index ? 1 : 0,
                  borderColor: 'divider',
                  display: 'flex',
                  gap: 1.5,
                  minHeight: 48,
                  pl: 2,
                  pr: 0.5,
                }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontFamily: fontFamilyMono,
                    minWidth: 0,
                    overflowWrap: 'anywhere',
                    py: 1,
                  }}
                >
                  {file.path.split('/').slice(-2).join('/')}
                </Typography>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}
                >
                  {formatBytes(file.size)}
                </Typography>
                <Tooltip title="Remove file">
                  <IconButton
                    color="error"
                    aria-label={`Delete ${file.path}`}
                    onClick={() => void onDeleteFile(file)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            ))}
          </Box>
        )}
      </EditorSection>

      <EditorSection
        title="Remove skill"
        description="Removes this skill and every file in its directory. Nothing changes until you publish."
        action={
          <Button
            color="error"
            variant="outlined"
            startIcon={<DeleteIcon />}
            onClick={() => void onDeleteSkill()}
          >
            Remove skill
          </Button>
        }
      />

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
