import { Alert, Box, Chip, Paper, Stack, Typography } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { DetailSection } from 'src/components/DetailDialog';
import {
  PluginFileInfo,
  PluginSkillItem,
  usePluginContents,
} from 'src/hooks/usePluginsApi';

// Package files that belong to the package rather than to any one skill.
const PACKAGE_ROOT_FILES = /^[^/]+$/;

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function FileList({
  files,
  strip = '',
}: {
  files: PluginFileInfo[];
  strip?: string;
}) {
  if (files.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No files.
      </Typography>
    );
  }
  return (
    <Stack spacing={0.25}>
      {files.map((file) => (
        <Stack
          key={file.path}
          direction="row"
          spacing={1}
          sx={{ alignItems: 'baseline' }}
        >
          <Typography
            variant="body2"
            sx={{ fontFamily: 'monospace', flex: 1, wordBreak: 'break-all' }}
          >
            {strip ? file.path.slice(strip.length) : file.path}
          </Typography>
          {file.executable && (
            <Chip
              label="exec"
              size="small"
              variant="outlined"
              color="warning"
            />
          )}
          <Typography variant="caption" color="text.secondary">
            {formatBytes(file.size)}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

function SkillCard({
  skill,
  files,
}: {
  skill: PluginSkillItem;
  files: PluginFileInfo[];
}) {
  const prefix = `${skill.source_path}/`;
  const skillFiles = files.filter((file) => file.path.startsWith(prefix));
  return (
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack
        direction="row"
        spacing={1}
        sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {skill.title}
        </Typography>
        <Chip
          label={skill.enabled ? 'Enabled' : 'Disabled'}
          color={skill.enabled ? 'success' : 'default'}
          size="small"
        />
      </Stack>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: 'block', fontFamily: 'monospace' }}
      >
        {skill.plugin_id}__{skill.skill_id}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: 'block', fontFamily: 'monospace', mb: 1 }}
      >
        {skill.source_path}/ (portable name {skill.portable_name})
      </Typography>
      <Typography variant="body2" sx={{ mb: 1 }}>
        {skill.description}
      </Typography>

      {skill.parameters.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Inputs
          </Typography>
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ flexWrap: 'wrap', gap: 0.5 }}
          >
            {skill.parameters.map((parameter) => (
              <Chip
                key={parameter.name}
                size="small"
                variant="outlined"
                sx={{ fontFamily: 'monospace' }}
                label={`${parameter.name}: ${parameter.type}${parameter.required ? '' : '?'}`}
              />
            ))}
          </Stack>
        </Box>
      )}

      {skill.allowed_tools.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Allowed tools
          </Typography>
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ flexWrap: 'wrap', gap: 0.5 }}
          >
            {skill.allowed_tools.map((tool) => (
              <Chip
                key={tool}
                label={tool}
                size="small"
                variant="outlined"
                sx={{ fontFamily: 'monospace' }}
              />
            ))}
          </Stack>
        </Box>
      )}

      {skill.triggers.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Triggers
          </Typography>
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ flexWrap: 'wrap', gap: 0.5 }}
          >
            {skill.triggers.map((trigger) => (
              <Chip key={trigger} label={trigger} size="small" />
            ))}
          </Stack>
        </Box>
      )}

      <Typography variant="caption" color="text.secondary">
        Files
      </Typography>
      <FileList files={skillFiles} strip={prefix} />
    </Paper>
  );
}

/**
 * One revision's skills and file structure.
 *
 * Shared by the list's detail dialog and the version-history dialog, so a
 * revision reads the same whether it is the current one or one you are deciding
 * whether to restore.
 */
export default function PluginContentsView({
  pluginId,
  revision,
}: {
  pluginId: string;
  revision: number;
}) {
  const { skills, files, loading, error } = usePluginContents(
    pluginId,
    revision,
  );

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
        <ConstellationSpinner />
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">{error.message}</Alert>;
  }

  const skillPrefixes = skills.map((skill) => `${skill.source_path}/`);
  const otherFiles = files.filter(
    (file) =>
      PACKAGE_ROOT_FILES.test(file.path) ||
      !skillPrefixes.some((prefix) => file.path.startsWith(prefix)),
  );

  return (
    <>
      <DetailSection title={`Skills (${skills.length})`}>
        {skills.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            This revision declares no skills.
          </Typography>
        ) : (
          <Stack spacing={1.5}>
            {skills.map((skill) => (
              <SkillCard key={skill.skill_id} skill={skill} files={files} />
            ))}
          </Stack>
        )}
      </DetailSection>

      <DetailSection title="Package files">
        <FileList files={otherFiles} />
      </DetailSection>
    </>
  );
}
