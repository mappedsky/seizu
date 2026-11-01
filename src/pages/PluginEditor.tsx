import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import PageTitle from 'src/components/PageTitle';
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Drawer,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { visuallyHidden } from '@mui/utils';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircle';
import CloseIcon from '@mui/icons-material/Close';
import ErrorOutlineIcon from '@mui/icons-material/Error';
import PublishIcon from '@mui/icons-material/Publish';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import {
  EditorSection,
  IdentityFact,
  IdentityFacts,
} from 'src/components/EditorSection';
import FieldWithHelp from 'src/components/FieldWithHelp';
import PluginPackageTree, {
  PackageTreeNode,
} from 'src/components/PluginPackageTree';
import PluginSkillEditor, {
  normalizeSkill,
} from 'src/components/PluginSkillEditor';
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
import { hueFor } from 'src/theme/brand';
import { fontFamilyMono } from 'src/theme/typography';

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
function filesOf(skill: StagedSkill, supporting: StagedFile[]): StagedFile[] {
  const prefix = `skills/${skill.document.portableName}/`;
  return supporting.filter((file) => file.path.startsWith(prefix));
}

/** The manifest without its per-skill entries, which belong to each skill. */
function manifestCore(manifest: PluginManifest): string {
  const core = cloneManifest(manifest);
  seizuExtension(core).skills = {};
  return serializeManifest(core);
}

/**
 * A skill's document, manifest entry and files, for per-skill change marks.
 * Compared as the skill editor stages them, so the defaults and spelling it
 * applies on opening a skill do not read as an edit.
 */
function skillSignature(skill: StagedSkill, supporting: StagedFile[]): string {
  const { document, extension } = normalizeSkill(
    skill.document,
    skill.extension,
  );
  return JSON.stringify([
    serializeSkillDocument(document),
    extension,
    filesOf(skill, supporting).map((file) => [
      file.path,
      file.sha256 ?? `staged:${file.size}`,
    ]),
  ]);
}

/** The published package, as the change marks compare against it. */
interface Baseline {
  manifest: string;
  skills: Record<string, string>;
}

const EMPTY_BASELINE: Baseline = { manifest: '', skills: {} };

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
    <Stack spacing={{ xs: 3, md: 4 }}>
      <Box>
        <Typography variant="h2" sx={{ fontSize: { xs: 24, md: 29 } }}>
          Plugin details
        </Typography>
        <Typography color="text.secondary" variant="body2" sx={{ mt: 0.5 }}>
          Written to the Agent Plugins 1.0.0 plugin.json manifest. Other
          extension namespaces are preserved.
        </Typography>
        <IdentityFacts>
          <IdentityFact
            label="Seizu namespace"
            value={deriveSeizuId(manifest.name ?? '') ?? '—'}
          />
          <IdentityFact label="Schema" value={manifest.$schema ?? '—'} />
        </IdentityFacts>
      </Box>

      <EditorSection
        title="Package"
        description="The package name also fixes the Seizu namespace every skill's tool name starts with."
      >
        <Stack spacing={2}>
          <Box sx={twoColumnSx}>
            <TextField
              label="Package name"
              value={manifest.name ?? ''}
              onChange={(event) => update({ name: event.target.value })}
              error={!manifest.name?.trim()}
              helperText={
                !manifest.name?.trim() ? 'Package name is required.' : ''
              }
              required
              fullWidth
            />
            <TextField
              label="Version"
              value={manifest.version ?? ''}
              onChange={(event) => update({ version: event.target.value })}
              error={!manifest.version?.trim()}
              helperText={
                !manifest.version?.trim() ? 'Version is required.' : ''
              }
              required
              fullWidth
            />
          </Box>
          <TextField
            label="Description"
            value={manifest.description ?? ''}
            onChange={(event) => update({ description: event.target.value })}
            multiline
            minRows={2}
            fullWidth
          />
        </Stack>
      </EditorSection>

      <EditorSection title="Author">
        <Box sx={threeColumnSx}>
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
        </Box>
      </EditorSection>

      <EditorSection
        title="Links and discovery"
        description="Where the package comes from and how it is found once exported."
      >
        <Stack spacing={2}>
          <Box sx={threeColumnSx}>
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
          </Box>
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
      </EditorSection>
    </Stack>
  );
}

const twoColumnSx = {
  display: 'grid',
  gap: 2,
  gridTemplateColumns: { xs: '1fr', sm: 'minmax(0, 2fr) minmax(0, 1fr)' },
} as const;

const threeColumnSx = {
  display: 'grid',
  gap: 2,
  gridTemplateColumns: { xs: '1fr', lg: 'repeat(3, minmax(0, 1fr))' },
} as const;

/** Where a diagnostic belongs in the package tree. */
function diagnosticTarget(
  item: PluginDiagnostic,
  skills: StagedSkill[],
): string {
  const owner = skills.find(
    (skill) =>
      (item.skill &&
        (item.skill === skill.document.portableName ||
          item.skill === effectiveSkillId(skill))) ||
      item.path?.startsWith(`skills/${skill.document.portableName}/`),
  );
  return owner ? owner.path : MANIFEST_PATH;
}

function ChecksPanel({
  message,
  diagnostics,
  skills,
  onSelect,
}: {
  message: string | null;
  diagnostics: PluginDiagnostic[];
  skills: StagedSkill[];
  onSelect: (path: string) => void;
}) {
  const theme = useTheme();
  const errors = diagnostics.filter((item) => item.severity === 'error');
  const warnings = diagnostics.length - errors.length;
  const tone = errors.length
    ? theme.palette.error.main
    : warnings
      ? theme.palette.warning.main
      : hueFor('aurora', theme.palette.mode);
  const Icon = errors.length
    ? ErrorOutlineIcon
    : warnings
      ? WarningAmberIcon
      : CheckCircleOutlineIcon;
  const counts = [
    errors.length &&
      `${errors.length} ${errors.length === 1 ? 'error' : 'errors'}`,
    warnings && `${warnings} ${warnings === 1 ? 'warning' : 'warnings'}`,
  ].filter(Boolean);

  return (
    <Box
      role="status"
      sx={{
        border: 1,
        borderColor: alpha(tone, 0.45),
        borderRadius: 1,
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          alignItems: 'center',
          bgcolor: alpha(tone, 0.08),
          display: 'flex',
          gap: 1.5,
          px: 2,
          py: 1.5,
        }}
      >
        <Icon sx={{ color: tone, flexShrink: 0 }} />
        <Typography variant="body2" sx={{ flex: 1, fontWeight: 600 }}>
          {message ?? 'Package checks'}
        </Typography>
        {counts.length > 0 && (
          <Typography
            variant="caption"
            sx={{ color: 'text.secondary', flexShrink: 0 }}
          >
            {counts.join(' · ')}
          </Typography>
        )}
      </Box>
      {diagnostics.length > 0 && (
        <Box component="ul" sx={{ listStyle: 'none', m: 0, p: 0 }}>
          {diagnostics.map((item) => {
            const target = diagnosticTarget(item, skills);
            const itemTone =
              item.severity === 'error'
                ? theme.palette.error.main
                : theme.palette.warning.main;
            return (
              <Box
                component="li"
                key={`${item.code}-${item.path || ''}-${item.skill || ''}-${item.message}`}
                sx={{ borderTop: 1, borderColor: 'divider' }}
              >
                <ButtonBase
                  onClick={() => onSelect(target)}
                  sx={{
                    alignItems: 'flex-start',
                    display: 'flex',
                    gap: 1.5,
                    justifyContent: 'flex-start',
                    px: 2,
                    py: 1.25,
                    textAlign: 'left',
                    width: '100%',
                    '&:hover': { bgcolor: 'action.hover' },
                    '&.Mui-focusVisible': {
                      outline: `2px solid ${theme.palette.primary.main}`,
                      outlineOffset: -2,
                    },
                  }}
                >
                  <Box
                    aria-hidden
                    sx={{
                      bgcolor: itemTone,
                      borderRadius: '50%',
                      flexShrink: 0,
                      height: 8,
                      mt: '7px',
                      width: 8,
                    }}
                  />
                  <Box sx={{ minWidth: 0 }}>
                    {item.path && (
                      <Typography
                        variant="caption"
                        component="div"
                        sx={{
                          color: 'text.secondary',
                          fontFamily: fontFamilyMono,
                          overflowWrap: 'anywhere',
                        }}
                      >
                        {item.path}
                      </Typography>
                    )}
                    <Typography variant="body2">
                      <Box component="span" sx={visuallyHidden}>
                        {item.severity}:{' '}
                      </Box>
                      {item.message}
                    </Typography>
                  </Box>
                </ButtonBase>
              </Box>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

export default function PluginEditor() {
  const { pluginId = '' } = useParams();
  const navigate = useNavigate();
  const theme = useTheme();
  // Below md the package tree moves into a drawer and the publish actions
  // into a bar pinned to the bottom of the screen.
  const compact = useMediaQuery(theme.breakpoints.down('md'));
  const api = usePluginMutations();
  const { tools: catalog, error: catalogError } = useToolCatalog();

  const [baseRevision, setBaseRevision] = useState<number | null>(null);
  const [manifest, setManifest] = useState<PluginManifest | null>(null);
  const [skills, setSkills] = useState<StagedSkill[]>([]);
  const [supporting, setSupporting] = useState<StagedFile[]>([]);
  const [baseline, setBaseline] = useState<Baseline>(EMPTY_BASELINE);
  const [selectedPath, setSelectedPath] = useState<string>(MANIFEST_PATH);
  const [diagnostics, setDiagnostics] = useState<PluginDiagnostic[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<'validate' | 'publish' | null>(null);
  const [newSkillOpen, setNewSkillOpen] = useState(false);
  const [contentsOpen, setContentsOpen] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [deleteSkillTarget, setDeleteSkillTarget] =
    useState<StagedSkill | null>(null);
  const [deleteFileTarget, setDeleteFileTarget] = useState<StagedFile | null>(
    null,
  );
  const busy = pending !== null;

  const payload = manifest ? packagePayload(manifest, skills, supporting) : [];

  // What the tree marks and the header counts: the manifest's own fields, and
  // each skill with its extension entry and its files. These cover everything
  // the editor can change, so they also decide whether there is anything to
  // publish.
  const manifestEdited =
    manifest !== null && manifestCore(manifest) !== baseline.manifest;
  const skillChange = (skill: StagedSkill): 'new' | 'modified' | null => {
    const before = baseline.skills[skill.path];
    if (before === undefined) return 'new';
    return before === skillSignature(skill, supporting) ? null : 'modified';
  };
  const removedSkills = Object.keys(baseline.skills).filter(
    (path) => !skills.some((skill) => skill.path === path),
  ).length;
  const changeCount =
    (manifestEdited ? 1 : 0) +
    skills.filter((skill) => skillChange(skill) !== null).length +
    removedSkills;
  const dirty = changeCount > 0;

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
      setBaseline({
        manifest: manifestCore(nextManifest),
        skills: Object.fromEntries(
          nextSkills.map((skill) => [
            skill.path,
            skillSignature(skill, nextSupporting),
          ]),
        ),
      });
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
    ? filesOf(selectedSkill, supporting)
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

  const select = (path: string) => {
    setSelectedPath(path);
    setContentsOpen(false);
  };

  // Diagnostics are attributed to the node they concern, so the tree shows
  // where the problems are before the panel is read.
  const tally = (path: string) => {
    const owned = diagnostics.filter(
      (item) => diagnosticTarget(item, skills) === path,
    );
    const errors = owned.filter((item) => item.severity === 'error').length;
    return { errors, warnings: owned.length - errors };
  };
  const plural = (count: number, word: string) =>
    `${count} ${word}${count === 1 ? '' : 's'}`;
  const anchorNode: PackageTreeNode = {
    path: MANIFEST_PATH,
    title: 'Plugin details',
    identity: MANIFEST_PATH,
    meta: manifest?.version ? [`version ${manifest.version}`] : [],
    change: manifestEdited ? 'modified' : null,
    ...tally(MANIFEST_PATH),
  };
  const skillNodes: PackageTreeNode[] = skills.map((skill) => {
    const files = filesOf(skill, supporting).length;
    const inputs = skill.extension.parameters?.length ?? 0;
    return {
      path: skill.path,
      title: skill.extension.title || skill.document.portableName,
      identity: effectiveSkillId(skill),
      tooltip: `${pluginId}__${effectiveSkillId(skill)}`,
      meta: [
        ...(inputs ? [plural(inputs, 'input')] : []),
        ...(files ? [plural(files, 'file')] : []),
      ],
      change: skillChange(skill),
      ...tally(skill.path),
    };
  });
  const selectedNode =
    skillNodes.find((node) => node.path === selectedPath) ?? anchorNode;

  const tree = (
    <PluginPackageTree
      anchor={anchorNode}
      skills={skillNodes}
      selectedPath={selectedPath}
      onSelect={select}
      onAddSkill={() => {
        setContentsOpen(false);
        setNewSkillOpen(true);
      }}
      addDisabled={busy || loading}
    />
  );

  const ember = hueFor('ember', theme.palette.mode);
  // Whether there is anything to publish, then the stored revision: the one
  // Publish would create, or the current one when there is nothing to publish.
  // A server revision number, not the manifest's version.
  const status = (
    <>
      {dirty ? (
        <strong>{plural(changeCount, 'unpublished change')}</strong>
      ) : (
        'No unpublished changes'
      )}
      {baseRevision !== null &&
        (dirty
          ? ` · publishes as revision ${baseRevision + 1}`
          : ` · revision ${baseRevision} is current`)}
    </>
  );

  // Cancel leaves without publishing; with staged edits it asks first.
  const actions = (
    <>
      <Button onClick={leave} disabled={busy} sx={{ px: compact ? 1 : 2 }}>
        Cancel
      </Button>
      <Button
        variant="outlined"
        startIcon={compact ? undefined : <CheckCircleOutlineIcon />}
        onClick={() => void validate()}
        disabled={busy || loading}
        fullWidth={compact}
        sx={{ px: compact ? 1 : undefined }}
      >
        {pending === 'validate' ? (
          <ConstellationSpinner size={20} />
        ) : (
          'Validate'
        )}
      </Button>
      <Button
        variant="contained"
        startIcon={compact ? undefined : <PublishIcon />}
        onClick={() => void publish()}
        disabled={busy || loading || !dirty || !!duplicateSkillId}
        fullWidth={compact}
      >
        {pending === 'publish' ? <ConstellationSpinner size={20} /> : 'Publish'}
      </Button>
    </>
  );

  const editor = (
    <Stack spacing={2.5}>
      {error && <Alert severity="error">{error}</Alert>}
      {duplicateSkillId && (
        <Alert severity="error">
          Two skills share the Seizu skill ID{' '}
          <strong>{duplicateSkillId}</strong>. Give each one a distinct ID
          before publishing.
        </Alert>
      )}
      {catalogError && (
        <Alert severity="warning">
          Tool catalog could not be loaded: {catalogError.message}
        </Alert>
      )}
      {(message || diagnostics.length > 0) && (
        <ChecksPanel
          message={message}
          diagnostics={diagnostics}
          skills={skills}
          onSelect={select}
        />
      )}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}>
          <ConstellationSpinner />
        </Box>
      ) : (
        <>
          {selectedPath === MANIFEST_PATH && manifest && (
            <PluginManifestEditor manifest={manifest} onChange={setManifest} />
          )}
          {selectedSkill && (
            <PluginSkillEditor
              key={selectedSkill.path}
              document={selectedSkill.document}
              extension={selectedSkill.extension}
              catalog={catalog}
              qualifiedName={`${pluginId}__${effectiveSkillId(selectedSkill)}`}
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
                  supporting.find((item) => item.path === file.path) ?? null,
                )
              }
              onDeleteSkill={async () => setDeleteSkillTarget(selectedSkill)}
            />
          )}
        </>
      )}
    </Stack>
  );

  return (
    <>
      <PageTitle>{`Edit ${pluginId} | Seizu`}</PageTitle>
      <Box
        sx={
          compact
            ? { pb: 12 }
            : {
                display: 'flex',
                flexDirection: 'column',
                height: `calc(100vh - ${DASHBOARD_NAVBAR_HEIGHT}px)`,
                overflow: 'hidden',
              }
        }
      >
        <Box
          component="header"
          sx={{
            alignItems: 'center',
            borderBottom: compact ? 0 : 1,
            borderColor: 'divider',
            display: 'flex',
            flexShrink: 0,
            gap: { xs: 1, md: 2 },
            pl: { xs: 1, md: 2 },
            pr: { xs: 2, md: 3 },
            pt: { xs: 2, md: 2.5 },
            pb: { xs: 1.5, md: 2.5 },
          }}
        >
          <Tooltip title="Back to Agent Plugins">
            <IconButton
              aria-label="Back to Agent Plugins"
              onClick={leave}
              sx={{ alignSelf: 'flex-start', mt: { xs: 0, md: 0.25 } }}
            >
              <ArrowBackIcon />
            </IconButton>
          </Tooltip>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Box
              sx={{
                alignItems: 'baseline',
                columnGap: 1.5,
                display: 'flex',
                flexWrap: 'wrap',
              }}
            >
              <Typography
                variant="h1"
                noWrap
                sx={{ fontSize: { xs: 26, md: 35 }, maxWidth: '100%' }}
              >
                {manifest?.name || pluginId}
              </Typography>
              <Typography
                variant="body2"
                noWrap
                sx={{
                  color: 'text.secondary',
                  fontFamily: fontFamilyMono,
                  maxWidth: '100%',
                }}
              >
                {pluginId}
              </Typography>
            </Box>
            <Box
              sx={{
                alignItems: 'flex-start',
                display: 'flex',
                gap: 1,
                mt: 0.75,
              }}
            >
              <Box
                aria-hidden
                sx={{
                  bgcolor: dirty ? ember : theme.palette.primary.main,
                  borderRadius: '50%',
                  flexShrink: 0,
                  height: 8,
                  // Centered on the first line when the status wraps.
                  mt: `calc((${theme.typography.body2.lineHeight}em - 8px) / 2)`,
                  width: 8,
                }}
              />
              <Typography
                variant="body2"
                sx={{ color: 'text.secondary', minWidth: 0 }}
              >
                {status}
              </Typography>
            </Box>
          </Box>
          {!compact && (
            <Stack direction="row" spacing={1.5} sx={{ flexShrink: 0 }}>
              {actions}
            </Stack>
          )}
        </Box>

        {compact ? (
          <>
            <Box
              sx={{
                bgcolor: 'background.default',
                borderBottom: 1,
                borderTop: 1,
                borderColor: 'divider',
                px: 2,
                py: 1,
              }}
            >
              <ButtonBase
                onClick={() => setContentsOpen(true)}
                aria-haspopup="dialog"
                aria-label={`Package contents: ${selectedNode.title}`}
                sx={{
                  alignItems: 'center',
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  display: 'flex',
                  gap: 1.5,
                  justifyContent: 'flex-start',
                  minHeight: 52,
                  px: 1.5,
                  textAlign: 'left',
                  width: '100%',
                  '&.Mui-focusVisible': {
                    outline: `2px solid ${theme.palette.primary.main}`,
                  },
                }}
              >
                <Box
                  aria-hidden
                  sx={{
                    bgcolor: selectedNode.change
                      ? ember
                      : theme.palette.primary.main,
                    borderRadius: '50%',
                    flexShrink: 0,
                    height: 12,
                    width: 12,
                  }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap sx={{ fontWeight: 700 }}>
                    {selectedNode.title}
                  </Typography>
                  <Typography
                    variant="caption"
                    noWrap
                    component="div"
                    sx={{ color: 'text.secondary', fontFamily: fontFamilyMono }}
                  >
                    {selectedNode.identity}
                  </Typography>
                </Box>
                <Typography
                  variant="caption"
                  sx={{ color: 'text.secondary', flexShrink: 0 }}
                >
                  {plural(skills.length, 'skill')}
                </Typography>
                <UnfoldMoreIcon sx={{ color: 'text.secondary' }} />
              </ButtonBase>
            </Box>
            <Box sx={{ px: 2, py: 3 }}>{editor}</Box>
            <Box
              sx={{
                bgcolor: 'background.default',
                borderTop: 1,
                borderColor: 'divider',
                bottom: 0,
                display: 'grid',
                gap: { xs: 1, sm: 1.5 },
                gridTemplateColumns: 'auto auto 1fr 1fr',
                left: 0,
                pb: 'calc(12px + env(safe-area-inset-bottom))',
                position: 'fixed',
                pt: 1.5,
                px: 2,
                right: 0,
                zIndex: theme.zIndex.appBar,
              }}
            >
              {/* The switcher scrolls away with the page; this keeps the
                  package tree one tap away from anywhere in a long skill. */}
              <Tooltip title="Package contents">
                <IconButton
                  aria-label="Open package contents"
                  onClick={() => setContentsOpen(true)}
                  sx={{ border: 1, borderColor: 'divider', borderRadius: 1 }}
                >
                  <AccountTreeIcon />
                </IconButton>
              </Tooltip>
              {actions}
            </Box>
            <Drawer
              anchor="bottom"
              open={contentsOpen}
              onClose={() => setContentsOpen(false)}
              slotProps={{
                paper: {
                  'aria-label': 'Package contents',
                  sx: {
                    backgroundImage: 'none',
                    bgcolor: 'background.default',
                    borderTopLeftRadius: 12,
                    borderTopRightRadius: 12,
                    maxHeight: '80vh',
                    pb: 'env(safe-area-inset-bottom)',
                  },
                },
              }}
            >
              <Box
                sx={{
                  alignItems: 'center',
                  borderBottom: 1,
                  borderColor: 'divider',
                  display: 'flex',
                  justifyContent: 'space-between',
                  pl: 2,
                  pr: 1,
                  py: 1,
                }}
              >
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                  Package contents
                </Typography>
                <IconButton
                  aria-label="Close package contents"
                  onClick={() => setContentsOpen(false)}
                >
                  <CloseIcon />
                </IconButton>
              </Box>
              <Box sx={{ overflowY: 'auto' }}>{tree}</Box>
            </Drawer>
          </>
        ) : (
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
                width: 300,
              }}
            >
              <Box
                sx={{
                  alignItems: 'baseline',
                  display: 'flex',
                  justifyContent: 'space-between',
                  pt: 2,
                  px: 2.5,
                }}
              >
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                  Package contents
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {plural(skills.length, 'skill')}
                </Typography>
              </Box>
              <Box sx={{ flex: 1, overflowX: 'hidden', overflowY: 'auto' }}>
                {tree}
              </Box>
            </Box>
            <Box sx={{ flex: 1, minWidth: 0, overflowY: 'auto' }}>
              <Box sx={{ maxWidth: 1080, px: { md: 4, xl: 6 }, py: 4 }}>
                {editor}
              </Box>
            </Box>
          </Box>
        )}

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
          cancelLabel="Keep editing"
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
