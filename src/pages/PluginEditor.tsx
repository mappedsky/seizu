import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircle';
import DeleteIcon from '@mui/icons-material/Delete';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlineOutlined';
import PublishIcon from '@mui/icons-material/Publish';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import PluginSkillEditor from 'src/components/PluginSkillEditor';
import {
  PluginDiagnostic,
  PluginFileInfo,
  usePluginMutations,
} from 'src/hooks/usePluginsApi';
import { useToolCatalog } from 'src/hooks/useToolsetsApi';
import {
  LOWER_SNAKE_ID,
  PluginManifest,
  PluginSkillExtension,
  PORTABLE_SKILL_NAME,
  SkillDocument,
  parseManifest,
  parseSkillDocument,
  seizuExtension,
  serializeManifest,
  serializeSkillDocument,
} from 'src/pluginAuthoring';

interface LoadedSkill {
  path: string;
  file: PluginFileInfo;
  document: SkillDocument;
  extension: PluginSkillExtension;
}

function cloneManifest(manifest: PluginManifest): PluginManifest {
  return JSON.parse(JSON.stringify(manifest)) as PluginManifest;
}

function FieldWithHelp({
  label,
  tooltip,
  children,
}: {
  label: string;
  tooltip: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, flex: 1 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
      <Tooltip title={tooltip} placement="top" arrow describeChild>
        <IconButton aria-label={`Help for ${label}`} size="small">
          <HelpOutlineIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

function NewSkillDialog({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (
    document: SkillDocument,
    extension: PluginSkillExtension,
  ) => Promise<void>;
}) {
  const [portableName, setPortableName] = useState('');
  const [skillId, setSkillId] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updatePortableName = (value: string) => {
    setPortableName(value);
    setSkillId(value.replaceAll('-', '_'));
  };

  const create = async () => {
    const portable = portableName.trim();
    const id = skillId.trim();
    if (!PORTABLE_SKILL_NAME.test(portable)) {
      setError('Portable name must use lowercase words separated by hyphens.');
      return;
    }
    if (!LOWER_SNAKE_ID.test(id) || id.length > 31) {
      setError('Skill ID must be lower_snake_case and at most 31 characters.');
      return;
    }
    if (!description.trim()) {
      setError('Description is required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onCreate(
        {
          portableName: portable,
          description: description.trim(),
          allowedTools: [],
          body: '# Instructions\n\nDescribe how the agent should use this skill.',
        },
        {
          skillId: id,
          title: title.trim() || undefined,
          enabled: true,
          triggers: [],
          parameters: [],
          aliases: [],
        },
      );
      onClose();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New skill</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          <FieldWithHelp
            label="Portable name"
            tooltip="Agent Skills directory and front matter name, such as scan-repository."
          >
            <TextField
              label="Portable name"
              value={portableName}
              onChange={(event) => updatePortableName(event.target.value)}
              required
              fullWidth
              size="small"
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
              size="small"
            />
          </FieldWithHelp>
          <TextField
            label="Display title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            fullWidth
            size="small"
          />
          <TextField
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            multiline
            minRows={2}
            required
            fullWidth
            size="small"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => void create()}
          disabled={saving}
        >
          {saving ? <ConstellationSpinner size={20} /> : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function PluginManifestEditor({
  manifest,
  onSave,
}: {
  manifest: PluginManifest;
  onSave: (manifest: PluginManifest) => Promise<void>;
}) {
  const extension = seizuExtension(manifest);
  const [name, setName] = useState(manifest.name ?? '');
  const [version, setVersion] = useState(manifest.version ?? '');
  const [description, setDescription] = useState(manifest.description ?? '');
  const [authorName, setAuthorName] = useState(manifest.author?.name ?? '');
  const [authorEmail, setAuthorEmail] = useState(manifest.author?.email ?? '');
  const [authorUrl, setAuthorUrl] = useState(manifest.author?.url ?? '');
  const [homepage, setHomepage] = useState(manifest.homepage ?? '');
  const [repository, setRepository] = useState(manifest.repository ?? '');
  const [license, setLicense] = useState(manifest.license ?? '');
  const [keywords, setKeywords] = useState(
    (manifest.keywords ?? []).join(', '),
  );
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!name.trim()) {
      setError('Package name is required.');
      return;
    }
    if (!version.trim()) {
      setError('Version is required.');
      return;
    }
    const next = cloneManifest(manifest);
    next.name = name.trim();
    next.version = version.trim();
    next.description = description.trim();
    next.homepage = homepage.trim();
    next.repository = repository.trim();
    next.license = license.trim();
    next.keywords = keywords
      .split(',')
      .map((keyword) => keyword.trim())
      .filter(Boolean);
    const author = {
      name: authorName.trim(),
      email: authorEmail.trim(),
      url: authorUrl.trim(),
    };
    next.author = Object.fromEntries(
      Object.entries(author).filter(([, value]) => value),
    );
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      await onSave(next);
      setMessage('Plugin details saved to the draft.');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Stack spacing={2.5}>
      {error && <Alert severity="error">{error}</Alert>}
      {message && <Alert severity="success">{message}</Alert>}
      <Box>
        <Typography variant="h2">Plugin details</Typography>
        <Typography color="text.secondary">
          These fields are written to the Agent Plugins 1.0.0 plugin.json
          manifest. Other extension namespaces are preserved.
        </Typography>
      </Box>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField label="Schema" value={manifest.$schema} disabled fullWidth />
        <TextField
          label="Seizu namespace"
          value={extension.skillsetId}
          disabled
          fullWidth
        />
      </Stack>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField
          label="Package name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          fullWidth
        />
        <TextField
          label="Version"
          value={version}
          onChange={(event) => setVersion(event.target.value)}
          required
          fullWidth
        />
      </Stack>
      <TextField
        label="Description"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        multiline
        minRows={2}
        fullWidth
      />
      <Divider />
      <Typography variant="subtitle2">Author</Typography>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField
          label="Name"
          value={authorName}
          onChange={(event) => setAuthorName(event.target.value)}
          fullWidth
        />
        <TextField
          label="Email"
          value={authorEmail}
          onChange={(event) => setAuthorEmail(event.target.value)}
          fullWidth
        />
        <TextField
          label="URL"
          value={authorUrl}
          onChange={(event) => setAuthorUrl(event.target.value)}
          fullWidth
        />
      </Stack>
      <Divider />
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField
          label="Homepage"
          value={homepage}
          onChange={(event) => setHomepage(event.target.value)}
          fullWidth
        />
        <TextField
          label="Repository"
          value={repository}
          onChange={(event) => setRepository(event.target.value)}
          fullWidth
        />
        <TextField
          label="License"
          value={license}
          onChange={(event) => setLicense(event.target.value)}
          fullWidth
        />
      </Stack>
      <FieldWithHelp
        label="Keywords"
        tooltip="Enter multiple keywords separated by commas."
      >
        <TextField
          label="Keywords"
          value={keywords}
          onChange={(event) => setKeywords(event.target.value)}
          fullWidth
        />
      </FieldWithHelp>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={saving}
        >
          Save to draft
        </Button>
      </Box>
    </Stack>
  );
}

export default function PluginEditor() {
  const { pluginId = '' } = useParams();
  const navigate = useNavigate();
  const api = usePluginMutations();
  const { tools: catalog, error: catalogError } = useToolCatalog();
  const [files, setFiles] = useState<PluginFileInfo[]>([]);
  const [manifest, setManifest] = useState<PluginManifest | null>(null);
  const [skills, setSkills] = useState<LoadedSkill[]>([]);
  const [selectedPath, setSelectedPath] = useState<string>('plugin.json');
  const [diagnostics, setDiagnostics] = useState<PluginDiagnostic[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [newSkillOpen, setNewSkillOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [deleteSkillTarget, setDeleteSkillTarget] =
    useState<LoadedSkill | null>(null);
  const [deleteFileTarget, setDeleteFileTarget] =
    useState<PluginFileInfo | null>(null);

  const load = async (preferredPath?: string) => {
    setLoading(true);
    setError(null);
    try {
      const nextFiles = await api.listDraftFiles(pluginId);
      const manifestInfo = nextFiles.find(
        (file) => file.path === 'plugin.json',
      );
      if (!manifestInfo) throw new Error('Draft is missing plugin.json.');
      const manifestBody = await api.readDraftFile(pluginId, 'plugin.json');
      const nextManifest = parseManifest(
        new TextDecoder().decode(manifestBody.bytes),
      );
      const extension = seizuExtension(nextManifest);
      const skillFiles = nextFiles.filter((file) =>
        /^skills\/[^/]+\/SKILL\.md$/.test(file.path),
      );
      const nextSkills = await Promise.all(
        skillFiles.map(async (file): Promise<LoadedSkill> => {
          const body = await api.readDraftFile(pluginId, file.path);
          const document = parseSkillDocument(
            new TextDecoder().decode(body.bytes),
          );
          return {
            path: file.path,
            file,
            document,
            extension: extension.skills[document.portableName] ?? {},
          };
        }),
      );
      setFiles(nextFiles);
      setManifest(nextManifest);
      setSkills(nextSkills);
      const desired = preferredPath ?? selectedPath;
      setSelectedPath(
        desired === 'plugin.json' ||
          nextSkills.some((skill) => skill.path === desired)
          ? desired
          : (nextSkills[0]?.path ?? 'plugin.json'),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load('plugin.json');
    // The mutation helper is intentionally not a dependency: it is recreated
    // with render state, while the draft is keyed only by the route parameter.
  }, [pluginId]);

  const replaceFileInfo = (updated: PluginFileInfo) => {
    setFiles((items) =>
      [...items.filter((item) => item.path !== updated.path), updated].sort(
        (left, right) => left.path.localeCompare(right.path),
      ),
    );
  };

  const writeBytes = async (
    path: string,
    bytes: Uint8Array,
    mediaType: string,
    executable = false,
  ) => {
    const existing = files.find((file) => file.path === path);
    const updated = await api.writeDraftFile(
      pluginId,
      path,
      bytes,
      mediaType,
      existing?.etag,
      executable,
    );
    replaceFileInfo(updated);
    return updated;
  };

  const writeManifest = async (next: PluginManifest) => {
    await writeBytes(
      'plugin.json',
      new TextEncoder().encode(serializeManifest(next)),
      'application/json',
    );
    setManifest(next);
  };

  const saveSkill = async (
    skill: LoadedSkill,
    document: SkillDocument,
    extension: PluginSkillExtension,
  ) => {
    if (!manifest) return;
    const nextManifest = cloneManifest(manifest);
    const extensionConfig = seizuExtension(nextManifest);
    if (
      Object.entries(extensionConfig.skills).some(
        ([portableName, item]) =>
          portableName !== document.portableName &&
          item.skillId === extension.skillId,
      )
    ) {
      throw new Error('A skill with that Seizu skill ID already exists.');
    }
    extensionConfig.skills[document.portableName] = extension;
    await writeBytes(
      skill.path,
      new TextEncoder().encode(serializeSkillDocument(document)),
      'text/markdown',
    );
    await writeManifest(nextManifest);
    setSkills((items) =>
      items.map((item) =>
        item.path === skill.path ? { ...item, document, extension } : item,
      ),
    );
  };

  const createSkill = async (
    document: SkillDocument,
    extension: PluginSkillExtension,
  ) => {
    if (!manifest) return;
    const path = `skills/${document.portableName}/SKILL.md`;
    if (files.some((file) => file.path === path)) {
      throw new Error('A skill with that portable name already exists.');
    }
    if (
      Object.values(seizuExtension(manifest).skills).some(
        (item) => item.skillId === extension.skillId,
      )
    ) {
      throw new Error('A skill with that Seizu skill ID already exists.');
    }
    const nextManifest = cloneManifest(manifest);
    seizuExtension(nextManifest).skills[document.portableName] = extension;
    await writeBytes(
      path,
      new TextEncoder().encode(serializeSkillDocument(document)),
      'text/markdown',
    );
    await writeManifest(nextManifest);
    await load(path);
  };

  const selectedSkill = skills.find((skill) => skill.path === selectedPath);
  const supportingFiles = selectedSkill
    ? files.filter(
        (file) =>
          file.path.startsWith(
            `skills/${selectedSkill.document.portableName}/`,
          ) && file.path !== selectedSkill.path,
      )
    : [];

  const uploadSupportingFile = async (
    directory: 'references' | 'scripts' | 'assets',
    file: File,
  ) => {
    if (!selectedSkill) return;
    const path = `skills/${selectedSkill.document.portableName}/${directory}/${file.name}`;
    await writeBytes(
      path,
      new Uint8Array(await file.arrayBuffer()),
      file.type || 'application/octet-stream',
      directory === 'scripts',
    );
    setMessage(`Saved ${path} to the draft.`);
  };

  const createSupportingTextFile = async (
    directory: 'references' | 'scripts' | 'assets',
    filename: string,
    content: string,
  ) => {
    if (!selectedSkill) return;
    const path = `skills/${selectedSkill.document.portableName}/${directory}/${filename}`;
    if (files.some((file) => file.path === path)) {
      throw new Error('A supporting file with that name already exists.');
    }
    await writeBytes(
      path,
      new TextEncoder().encode(content),
      'text/plain',
      directory === 'scripts',
    );
    setMessage(`Saved ${path} to the draft.`);
  };

  const confirmDeleteFile = async () => {
    if (!deleteFileTarget) return;
    await api.deleteDraftFile(
      pluginId,
      deleteFileTarget.path,
      deleteFileTarget.etag,
    );
    setFiles((items) =>
      items.filter((item) => item.path !== deleteFileTarget.path),
    );
    setDeleteFileTarget(null);
  };

  const confirmDeleteSkill = async () => {
    if (!deleteSkillTarget || !manifest) return;
    setBusy(true);
    try {
      const prefix = `skills/${deleteSkillTarget.document.portableName}/`;
      for (const file of files.filter((item) => item.path.startsWith(prefix))) {
        await api.deleteDraftFile(pluginId, file.path, file.etag);
      }
      const nextManifest = cloneManifest(manifest);
      delete seizuExtension(nextManifest).skills[
        deleteSkillTarget.document.portableName
      ];
      await writeManifest(nextManifest);
      setDeleteSkillTarget(null);
      await load('plugin.json');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const validate = async () => {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const result = await api.validateDraft(pluginId);
      setDiagnostics(result.diagnostics);
      setMessage(
        result.valid ? 'Draft is valid.' : 'Draft has blocking errors.',
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const result = await api.validateDraft(pluginId);
      setDiagnostics(result.diagnostics);
      if (!result.valid) {
        setMessage('Draft has blocking errors.');
        return;
      }
      await api.publishDraft(pluginId);
      navigate('/app/plugins');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    setBusy(true);
    try {
      await api.discardDraft(pluginId);
      navigate('/app/plugins');
    } catch (reason) {
      setError((reason as Error).message);
      setDiscardOpen(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: 'calc(100vh - 64px)',
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          alignItems: { xs: 'stretch', md: 'center' },
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
          position: 'sticky',
          top: 0,
          zIndex: 10,
        }}
      >
        <Box>
          <Typography variant="h1">Edit {pluginId}</Typography>
          <Typography color="text.secondary" variant="body2">
            Field edits stay in this browser until saved to the server-side
            draft. Publish validates and promotes the complete package.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
          <Button
            variant="outlined"
            startIcon={<CheckCircleOutlineIcon />}
            onClick={() => void validate()}
            disabled={busy || loading}
          >
            Validate
          </Button>
          <Button
            color="error"
            startIcon={<DeleteIcon />}
            onClick={() => setDiscardOpen(true)}
            disabled={busy || loading}
          >
            Discard draft
          </Button>
          <Button
            variant="contained"
            startIcon={<PublishIcon />}
            onClick={() => void publish()}
            disabled={busy || loading}
          >
            Publish
          </Button>
        </Stack>
      </Box>

      <Box sx={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Box
          sx={{
            borderRight: 1,
            borderColor: 'divider',
            display: 'flex',
            flexDirection: 'column',
            flexShrink: 0,
            overflow: 'hidden',
            width: 260,
          }}
        >
          <Box
            sx={{
              alignItems: 'center',
              borderBottom: 1,
              borderColor: 'divider',
              display: 'flex',
              flexShrink: 0,
              justifyContent: 'space-between',
              minHeight: 40,
              px: 1.5,
            }}
          >
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontWeight: 700,
                letterSpacing: 0.8,
              }}
            >
              PACKAGE CONTENT
            </Typography>
            <Tooltip title="Add skill" placement="right">
              <span>
                <IconButton
                  size="small"
                  aria-label="Add skill"
                  onClick={() => setNewSkillOpen(true)}
                  disabled={busy || loading}
                >
                  <AddIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Box>
          <List disablePadding sx={{ flex: 1, overflowY: 'auto' }}>
            <ListItemButton
              selected={selectedPath === 'plugin.json'}
              onClick={() => setSelectedPath('plugin.json')}
            >
              <ListItemText primary="Plugin details" secondary="plugin.json" />
            </ListItemButton>
            {skills.map((skill) => (
              <ListItemButton
                key={skill.path}
                selected={selectedPath === skill.path}
                onClick={() => setSelectedPath(skill.path)}
              >
                <ListItemText
                  primary={skill.extension.title || skill.document.portableName}
                  secondary={`${pluginId}__${skill.extension.skillId || skill.document.portableName.replaceAll('-', '_')}`}
                />
              </ListItemButton>
            ))}
          </List>
        </Box>

        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            overflowY: 'auto',
            p: { xs: 2, md: 3 },
          }}
        >
          <Stack spacing={2}>
            {error && <Alert severity="error">{error}</Alert>}
            {catalogError && (
              <Alert severity="warning">
                Tool catalog could not be loaded: {catalogError.message}
              </Alert>
            )}
            {message && (
              <Alert
                severity={
                  diagnostics.some((item) => item.severity === 'error')
                    ? 'error'
                    : 'info'
                }
              >
                {message}
              </Alert>
            )}
            {diagnostics.map((item) => (
              <Alert
                key={`${item.code}-${item.path || ''}-${item.skill || ''}-${item.message}`}
                severity={item.severity}
              >
                {item.path ? `${item.path}: ` : ''}
                {item.message}
              </Alert>
            ))}

            {loading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
                <ConstellationSpinner />
              </Box>
            ) : (
              <>
                {selectedPath === 'plugin.json' && manifest && (
                  <PluginManifestEditor
                    key={serializeManifest(manifest)}
                    manifest={cloneManifest(manifest)}
                    onSave={writeManifest}
                  />
                )}
                {selectedSkill && (
                  <PluginSkillEditor
                    key={`${selectedSkill.path}:${selectedSkill.file.etag}`}
                    document={selectedSkill.document}
                    extension={selectedSkill.extension}
                    catalog={catalog}
                    supportingFiles={supportingFiles}
                    onSave={(document, extension) =>
                      saveSkill(selectedSkill, document, extension)
                    }
                    onUpload={uploadSupportingFile}
                    onCreateTextFile={createSupportingTextFile}
                    onDeleteFile={async (file) => setDeleteFileTarget(file)}
                    onDeleteSkill={async () =>
                      setDeleteSkillTarget(selectedSkill)
                    }
                  />
                )}
              </>
            )}
          </Stack>
        </Box>
      </Box>

      <NewSkillDialog
        key={newSkillOpen ? 'open' : 'closed'}
        open={newSkillOpen}
        onClose={() => setNewSkillOpen(false)}
        onCreate={createSkill}
      />
      <ConfirmDeleteDialog
        open={!!deleteFileTarget}
        title="Remove supporting file from draft?"
        confirmLabel="Remove"
        onClose={() => setDeleteFileTarget(null)}
        onConfirm={() => void confirmDeleteFile()}
      >
        Remove <strong>{deleteFileTarget?.path}</strong> from this draft?
      </ConfirmDeleteDialog>
      <ConfirmDeleteDialog
        open={!!deleteSkillTarget}
        title="Remove skill from draft?"
        confirmLabel="Remove"
        deleting={busy}
        onClose={() => setDeleteSkillTarget(null)}
        onConfirm={() => void confirmDeleteSkill()}
      >
        Remove <strong>{deleteSkillTarget?.document.portableName}</strong> and
        every file in its package directory from this draft?
      </ConfirmDeleteDialog>
      <ConfirmDeleteDialog
        open={discardOpen}
        title="Discard draft?"
        confirmLabel="Discard"
        deleting={busy}
        onClose={() => setDiscardOpen(false)}
        onConfirm={() => void discard()}
      >
        Discard every unpublished change to <strong>{pluginId}</strong>?
      </ConfirmDeleteDialog>
    </Box>
  );
}
