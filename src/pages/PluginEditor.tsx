import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Helmet } from 'react-helmet';
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
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircle';
import PublishIcon from '@mui/icons-material/Publish';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import FieldWithHelp from 'src/components/FieldWithHelp';
import PluginSkillEditor from 'src/components/PluginSkillEditor';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';
import {
  PluginDiagnostic,
  StagedFilePayload,
  usePluginMutations,
} from 'src/hooks/usePluginsApi';
import { useToolCatalog } from 'src/hooks/useToolsetsApi';
import {
  LOWER_SNAKE_ID,
  PluginManifest,
  PluginSkillExtension,
  PORTABLE_SKILL_NAME,
  SkillDocument,
  deriveSeizuId,
  parseManifest,
  parseSkillDocument,
  seizuExtension,
  serializeManifest,
  serializeSkillDocument,
} from 'src/pluginAuthoring';

// The rail is a fixed 260px, so its rows must ellipsize: a long skill title or
// a long `plugin__skill` name would otherwise scroll the whole panel sideways.
const railRowSx = { minWidth: 0, my: 0.25 };
const railRowSlotProps = {
  primary: { noWrap: true },
  secondary: { noWrap: true, sx: { fontFamily: 'monospace', fontSize: 12 } },
} as const;

const MANIFEST_PATH = 'plugin.json';
const SKILL_FILE_RE = /^skills\/[^/]+\/SKILL\.md$/;

type SupportingDirectory = 'references' | 'scripts' | 'assets';

/**
 * A supporting file in the staged package.
 *
 * `bytes` is set for a file this session added; `sha256` for one carried over
 * from the published revision, which publish retains by digest rather than
 * round-tripping through the browser.
 */
interface StagedFile {
  path: string;
  mediaType: string;
  executable: boolean;
  size: number;
  bytes?: Uint8Array;
  sha256?: string;
}

interface StagedSkill {
  path: string;
  document: SkillDocument;
  extension: PluginSkillExtension;
}

function cloneManifest(manifest: PluginManifest): PluginManifest {
  return JSON.parse(JSON.stringify(manifest)) as PluginManifest;
}

function effectiveSkillId(skill: StagedSkill): string {
  return skill.document.portableName.replaceAll('-', '_');
}

/** The whole package as the publish/validate endpoints take it. */
function packagePayload(
  manifest: PluginManifest,
  skills: StagedSkill[],
  supporting: StagedFile[],
): StagedFilePayload[] {
  const encoder = new TextEncoder();
  const encode = (bytes: Uint8Array) => {
    let binary = '';
    const chunk = 0x8000;
    for (let index = 0; index < bytes.length; index += chunk) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
    }
    return btoa(binary);
  };
  return [
    {
      path: MANIFEST_PATH,
      media_type: 'application/json',
      content_base64: encode(encoder.encode(serializeManifest(manifest))),
    },
    ...skills.map((skill) => ({
      path: skill.path,
      media_type: 'text/markdown',
      content_base64: encode(
        encoder.encode(serializeSkillDocument(skill.document)),
      ),
    })),
    ...supporting.map((file) =>
      file.bytes
        ? {
            path: file.path,
            media_type: file.mediaType,
            executable: file.executable,
            content_base64: encode(file.bytes),
          }
        : {
            path: file.path,
            media_type: file.mediaType,
            executable: file.executable,
            sha256: file.sha256 as string,
          },
    ),
  ];
}

/** A cheap identity for the staged package, for unsaved-change detection. */
function packageFingerprint(payload: StagedFilePayload[]): string {
  return JSON.stringify(payload);
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
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const skillId = portableName.trim().replaceAll('-', '_');

  const create = async () => {
    const portable = portableName.trim();
    const id = skillId;
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
          title: title.trim() || undefined,
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
              onChange={(event) => setPortableName(event.target.value)}
              helperText={
                skillId ? `Seizu skill id will be ${skillId}` : undefined
              }
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
          <TextField
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            multiline
            minRows={2}
            required
            fullWidth
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
  onChange,
}: {
  manifest: PluginManifest;
  onChange: (manifest: PluginManifest) => void;
}) {
  const update = (patch: Partial<PluginManifest>) => {
    const next = cloneManifest(manifest);
    Object.assign(next, patch);
    onChange(next);
  };
  const author = manifest.author ?? {};
  const updateAuthor = (patch: Record<string, string>) => {
    const merged: Record<string, string> = { ...author, ...patch };
    update({
      author: Object.fromEntries(
        Object.entries(merged).filter(([, value]) => value),
      ),
    });
  };

  return (
    <Stack spacing={2.5}>
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
          value={deriveSeizuId(manifest.name ?? '') ?? '—'}
          helperText="Derived from the package name"
          disabled
          fullWidth
        />
      </Stack>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField
          label="Package name"
          value={manifest.name ?? ''}
          onChange={(event) => update({ name: event.target.value })}
          error={!manifest.name?.trim()}
          helperText={!manifest.name?.trim() ? 'Package name is required.' : ''}
          required
          fullWidth
        />
        <TextField
          label="Version"
          value={manifest.version ?? ''}
          onChange={(event) => update({ version: event.target.value })}
          error={!manifest.version?.trim()}
          helperText={!manifest.version?.trim() ? 'Version is required.' : ''}
          required
          fullWidth
        />
      </Stack>
      <TextField
        label="Description"
        value={manifest.description ?? ''}
        onChange={(event) => update({ description: event.target.value })}
        multiline
        minRows={2}
        fullWidth
      />
      <Divider />
      <Typography variant="subtitle2">Author</Typography>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField
          label="Name"
          value={author.name ?? ''}
          onChange={(event) => updateAuthor({ name: event.target.value })}
          fullWidth
        />
        <TextField
          label="Email"
          value={author.email ?? ''}
          onChange={(event) => updateAuthor({ email: event.target.value })}
          fullWidth
        />
        <TextField
          label="URL"
          value={author.url ?? ''}
          onChange={(event) => updateAuthor({ url: event.target.value })}
          fullWidth
        />
      </Stack>
      <Divider />
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <TextField
          label="Homepage"
          value={manifest.homepage ?? ''}
          onChange={(event) => update({ homepage: event.target.value })}
          fullWidth
        />
        <TextField
          label="Repository"
          value={manifest.repository ?? ''}
          onChange={(event) => update({ repository: event.target.value })}
          fullWidth
        />
        <TextField
          label="License"
          value={manifest.license ?? ''}
          onChange={(event) => update({ license: event.target.value })}
          fullWidth
        />
      </Stack>
      <FieldWithHelp
        label="Keywords"
        tooltip="Enter multiple keywords separated by commas."
      >
        <TextField
          label="Keywords"
          value={(manifest.keywords ?? []).join(', ')}
          onChange={(event) =>
            update({
              keywords: event.target.value
                .split(',')
                .map((keyword) => keyword.trim())
                .filter(Boolean),
            })
          }
          fullWidth
        />
      </FieldWithHelp>
    </Stack>
  );
}

export default function PluginEditor() {
  const { pluginId = '' } = useParams();
  const navigate = useNavigate();
  const api = usePluginMutations();
  const { tools: catalog, error: catalogError } = useToolCatalog();

  const [baseRevision, setBaseRevision] = useState<number | null>(null);
  const [manifest, setManifest] = useState<PluginManifest | null>(null);
  const [skills, setSkills] = useState<StagedSkill[]>([]);
  const [supporting, setSupporting] = useState<StagedFile[]>([]);
  const [baseline, setBaseline] = useState('');
  const [selectedPath, setSelectedPath] = useState<string>(MANIFEST_PATH);
  const [diagnostics, setDiagnostics] = useState<PluginDiagnostic[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<'validate' | 'publish' | null>(null);
  const [newSkillOpen, setNewSkillOpen] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [deleteSkillTarget, setDeleteSkillTarget] =
    useState<StagedSkill | null>(null);
  const [deleteFileTarget, setDeleteFileTarget] = useState<StagedFile | null>(
    null,
  );
  const busy = pending !== null;

  const payload = manifest ? packagePayload(manifest, skills, supporting) : [];
  const dirty = manifest !== null && packageFingerprint(payload) !== baseline;

  const duplicateSkillId = (() => {
    const seen = new Set<string>();
    for (const skill of skills) {
      const id = effectiveSkillId(skill);
      if (seen.has(id)) return id;
      seen.add(id);
    }
    return null;
  })();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const plugin = await api.get(pluginId);
      const infos = await api.listFiles(pluginId, plugin.current_revision);
      const manifestInfo = infos.find((file) => file.path === MANIFEST_PATH);
      if (!manifestInfo) throw new Error('Package is missing plugin.json.');
      const manifestBody = await api.readFile(
        pluginId,
        plugin.current_revision,
        MANIFEST_PATH,
      );
      const nextManifest = parseManifest(
        new TextDecoder().decode(manifestBody.bytes),
      );
      const extension = seizuExtension(nextManifest);
      const nextSkills = await Promise.all(
        infos
          .filter((file) => SKILL_FILE_RE.test(file.path))
          .map(async (file): Promise<StagedSkill> => {
            const body = await api.readFile(
              pluginId,
              plugin.current_revision,
              file.path,
            );
            const document = parseSkillDocument(
              new TextDecoder().decode(body.bytes),
            );
            return {
              path: file.path,
              document,
              extension: extension.skills[document.portableName] ?? {},
            };
          }),
      );
      // Supporting files are carried by digest: their bytes never enter the
      // browser, so a package with large assets stays cheap to edit.
      const nextSupporting = infos
        .filter(
          (file) =>
            file.path !== MANIFEST_PATH && !SKILL_FILE_RE.test(file.path),
        )
        .map((file) => ({
          path: file.path,
          mediaType: file.media_type,
          executable: file.executable,
          size: file.size,
          sha256: file.sha256,
        }));
      setBaseRevision(plugin.current_revision);
      setManifest(nextManifest);
      setSkills(nextSkills);
      setSupporting(nextSupporting);
      setBaseline(
        packageFingerprint(
          packagePayload(nextManifest, nextSkills, nextSupporting),
        ),
      );
      setSelectedPath(MANIFEST_PATH);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
    // The mutation helper is recreated with render state; the package is keyed
    // only by the route parameter.
  }, [pluginId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Edits live only in this tab until Publish, so closing it loses them.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  const selectedSkill = skills.find((skill) => skill.path === selectedPath);
  const skillSupportingFiles = selectedSkill
    ? supporting.filter((file) =>
        file.path.startsWith(`skills/${selectedSkill.document.portableName}/`),
      )
    : [];

  const updateSkill = (
    path: string,
    document: SkillDocument,
    extension: PluginSkillExtension,
  ) => {
    setSkills((items) =>
      items.map((item) =>
        item.path === path ? { ...item, document, extension } : item,
      ),
    );
    setManifest((current) => {
      if (!current) return current;
      const next = cloneManifest(current);
      seizuExtension(next).skills[document.portableName] = extension;
      return next;
    });
  };

  const createSkill = async (
    document: SkillDocument,
    extension: PluginSkillExtension,
  ) => {
    if (!manifest) return;
    const path = `skills/${document.portableName}/SKILL.md`;
    if (skills.some((skill) => skill.path === path)) {
      throw new Error('A skill with that portable name already exists.');
    }
    // Ids derive from portable names, so a duplicate name is the only way to
    // collide, and the check above already caught it.
    const next = cloneManifest(manifest);
    seizuExtension(next).skills[document.portableName] = extension;
    setManifest(next);
    setSkills((items) => [...items, { path, document, extension }]);
    setSelectedPath(path);
  };

  const addSupportingFile = (
    directory: SupportingDirectory,
    filename: string,
    bytes: Uint8Array,
    mediaType: string,
  ) => {
    if (!selectedSkill) return;
    const path = `skills/${selectedSkill.document.portableName}/${directory}/${filename}`;
    if (supporting.some((file) => file.path === path)) {
      throw new Error('A supporting file with that name already exists.');
    }
    setSupporting((items) => [
      ...items,
      {
        path,
        mediaType,
        executable: directory === 'scripts',
        size: bytes.length,
        bytes,
      },
    ]);
    setMessage(`Staged ${path}. Publish to apply it.`);
  };

  const confirmDeleteFile = () => {
    if (!deleteFileTarget) return;
    setSupporting((items) =>
      items.filter((item) => item.path !== deleteFileTarget.path),
    );
    setDeleteFileTarget(null);
  };

  const confirmDeleteSkill = () => {
    if (!deleteSkillTarget || !manifest) return;
    const prefix = `skills/${deleteSkillTarget.document.portableName}/`;
    const next = cloneManifest(manifest);
    delete seizuExtension(next).skills[deleteSkillTarget.document.portableName];
    setManifest(next);
    setSkills((items) =>
      items.filter((item) => item.path !== deleteSkillTarget.path),
    );
    setSupporting((items) =>
      items.filter((item) => !item.path.startsWith(prefix)),
    );
    setDeleteSkillTarget(null);
    setSelectedPath(MANIFEST_PATH);
  };

  const validate = async () => {
    setPending('validate');
    setMessage(null);
    setError(null);
    try {
      const result = await api.validatePackage(pluginId, payload);
      setDiagnostics(result.diagnostics);
      setMessage(
        result.valid
          ? 'Package is valid. Publish to apply it.'
          : 'Package has blocking errors.',
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  };

  const publish = async () => {
    if (baseRevision === null) return;
    setPending('publish');
    setMessage(null);
    setError(null);
    try {
      const result = await api.validatePackage(pluginId, payload);
      setDiagnostics(result.diagnostics);
      if (!result.valid) {
        setMessage('Package has blocking errors.');
        return;
      }
      await api.publishPackage(pluginId, payload, baseRevision);
      navigate('/app/plugins');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  };

  const leave = () => {
    if (dirty) {
      setLeaveOpen(true);
      return;
    }
    navigate('/app/plugins');
  };

  return (
    <>
      <Helmet>
        <title>{`Edit ${pluginId} | Seizu`}</title>
      </Helmet>
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          height: `calc(100vh - ${DASHBOARD_NAVBAR_HEIGHT}px)`,
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
            <Button
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={leave}
              sx={{ mb: 0.5, ml: -1 }}
            >
              Back to Agent Plugins
            </Button>
            <Typography variant="h1">Edit {pluginId}</Typography>
            <Typography color="text.secondary" variant="body2">
              {dirty
                ? 'Unpublished changes are held in this tab. Publish applies the whole package at once.'
                : `Published revision v${baseRevision ?? '—'}. Edits apply only when you publish.`}
            </Typography>
          </Box>
          <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
            <Button
              variant="outlined"
              startIcon={<CheckCircleOutlineIcon />}
              onClick={() => void validate()}
              disabled={busy || loading}
            >
              {pending === 'validate' ? (
                <ConstellationSpinner size={20} />
              ) : (
                'Validate'
              )}
            </Button>
            <Button
              variant="contained"
              startIcon={<PublishIcon />}
              onClick={() => void publish()}
              disabled={busy || loading || !dirty || !!duplicateSkillId}
            >
              {pending === 'publish' ? (
                <ConstellationSpinner size={20} />
              ) : (
                'Publish'
              )}
            </Button>
          </Stack>
        </Box>

        <Box
          sx={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}
        >
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
            <List
              disablePadding
              sx={{ flex: 1, overflowX: 'hidden', overflowY: 'auto' }}
            >
              <ListItemButton
                selected={selectedPath === MANIFEST_PATH}
                onClick={() => setSelectedPath(MANIFEST_PATH)}
              >
                <ListItemText
                  primary="Plugin details"
                  secondary={MANIFEST_PATH}
                  sx={railRowSx}
                  slotProps={railRowSlotProps}
                />
              </ListItemButton>
              {skills.map((skill) => {
                const label =
                  skill.extension.title || skill.document.portableName;
                const qualifiedName = `${pluginId}__${effectiveSkillId(skill)}`;
                return (
                  <Tooltip
                    key={skill.path}
                    title={`${label} — ${qualifiedName}`}
                    placement="right"
                    enterDelay={500}
                  >
                    <ListItemButton
                      selected={selectedPath === skill.path}
                      onClick={() => setSelectedPath(skill.path)}
                    >
                      <ListItemText
                        primary={label}
                        secondary={qualifiedName}
                        sx={railRowSx}
                        slotProps={railRowSlotProps}
                      />
                    </ListItemButton>
                  </Tooltip>
                );
              })}
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
              {duplicateSkillId && (
                <Alert severity="error">
                  Two skills share the Seizu skill ID{' '}
                  <strong>{duplicateSkillId}</strong>. Give each one a distinct
                  ID before publishing.
                </Alert>
              )}
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
                  {selectedPath === MANIFEST_PATH && manifest && (
                    <PluginManifestEditor
                      manifest={manifest}
                      onChange={setManifest}
                    />
                  )}
                  {selectedSkill && (
                    <PluginSkillEditor
                      key={selectedSkill.path}
                      document={selectedSkill.document}
                      extension={selectedSkill.extension}
                      catalog={catalog}
                      supportingFiles={skillSupportingFiles.map((file) => ({
                        path: file.path,
                        media_type: file.mediaType,
                        size: file.size,
                        sha256: file.sha256 ?? '',
                        executable: file.executable,
                        etag: file.sha256 ?? '',
                      }))}
                      onChange={(document, extension) =>
                        updateSkill(selectedSkill.path, document, extension)
                      }
                      onUpload={async (directory, file) =>
                        addSupportingFile(
                          directory,
                          file.name,
                          new Uint8Array(await file.arrayBuffer()),
                          file.type || 'application/octet-stream',
                        )
                      }
                      onCreateTextFile={async (directory, filename, content) =>
                        addSupportingFile(
                          directory,
                          filename,
                          new TextEncoder().encode(content),
                          'text/plain',
                        )
                      }
                      onDeleteFile={async (file) =>
                        setDeleteFileTarget(
                          supporting.find((item) => item.path === file.path) ??
                            null,
                        )
                      }
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
          title="Remove supporting file?"
          confirmLabel="Remove"
          onClose={() => setDeleteFileTarget(null)}
          onConfirm={confirmDeleteFile}
        >
          Remove <strong>{deleteFileTarget?.path}</strong> from this package? It
          is removed when you publish.
        </ConfirmDeleteDialog>
        <ConfirmDeleteDialog
          open={!!deleteSkillTarget}
          title="Remove skill?"
          confirmLabel="Remove"
          onClose={() => setDeleteSkillTarget(null)}
          onConfirm={confirmDeleteSkill}
        >
          Remove <strong>{deleteSkillTarget?.document.portableName}</strong> and
          every file in its package directory? It is removed when you publish.
        </ConfirmDeleteDialog>
        <ConfirmDeleteDialog
          open={leaveOpen}
          title="Discard unpublished changes?"
          confirmLabel="Discard"
          onClose={() => setLeaveOpen(false)}
          onConfirm={() => navigate('/app/plugins')}
        >
          Your edits to <strong>{pluginId}</strong> have not been published and
          are held only in this tab. Leaving discards them.
        </ConfirmDeleteDialog>
      </Box>
    </>
  );
}
