import { useState } from 'react';
import { Alert, Box, Chip, Stack, Typography } from '@mui/material';
import DetailDialog, {
  DetailCodeBlock,
  DetailSection,
} from 'src/components/DetailDialog';
import PluginContentsView from 'src/components/PluginContentsView';
import UserDisplay from 'src/components/UserDisplay';
import {
  PluginListItem,
  PluginSkillItem,
  usePluginMutations,
} from 'src/hooks/usePluginsApi';

interface Props {
  open: boolean;
  onClose: () => void;
  plugin: PluginListItem | null;
}

export default function PluginDetailDialog({ open, onClose, plugin }: Props) {
  const { setSkillEnabled } = usePluginMutations();
  const [failure, setFailure] = useState<string | null>(null);
  const [toggled, setToggled] = useState(0);
  if (!plugin) return null;

  const toggleSkill = async (skill: PluginSkillItem, enabled: boolean) => {
    setFailure(null);
    try {
      await setSkillEnabled(skill.plugin_id, skill.skill_id, enabled);
      // Re-read rather than patch in place: the server owns this state.
      setToggled((value) => value + 1);
    } catch (reason) {
      setFailure((reason as Error).message);
    }
  };

  return (
    <DetailDialog
      open={open}
      onClose={onClose}
      title={plugin.name}
      secondary={`v${plugin.current_revision}`}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        <DetailSection title="Namespace">
          <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
            {plugin.plugin_id}
          </Typography>
        </DetailSection>

        <DetailSection title="Status">
          <Chip
            label={plugin.enabled ? 'Enabled' : 'Disabled'}
            color={plugin.enabled ? 'success' : 'default'}
            size="small"
          />
        </DetailSection>

        {plugin.description && (
          <DetailSection title="Description">
            <Typography variant="body2">{plugin.description}</Typography>
          </DetailSection>
        )}

        <DetailSection title="Package version">
          <Typography variant="body2">
            {plugin.package_version || '—'}
          </Typography>
        </DetailSection>

        <DetailSection title="Package digest">
          <DetailCodeBlock>{plugin.package_digest || '—'}</DetailCodeBlock>
        </DetailSection>

        {plugin.diagnostics.length > 0 && (
          <DetailSection title="Diagnostics">
            <Stack spacing={1}>
              {plugin.diagnostics.map((diagnostic) => (
                <Alert
                  key={`${diagnostic.code}-${diagnostic.path ?? ''}-${diagnostic.skill ?? ''}-${diagnostic.message}`}
                  severity={diagnostic.severity}
                >
                  {diagnostic.path ? `${diagnostic.path}: ` : ''}
                  {diagnostic.message}
                </Alert>
              ))}
            </Stack>
          </DetailSection>
        )}

        <DetailSection title="Last updated">
          <Typography variant="body2">
            {new Date(plugin.updated_at).toLocaleString()} by{' '}
            <UserDisplay userId={plugin.updated_by || plugin.created_by} />
          </Typography>
        </DetailSection>

        <DetailSection title="Created">
          <Typography variant="body2">
            {new Date(plugin.created_at).toLocaleString()} by{' '}
            <UserDisplay userId={plugin.created_by} />
          </Typography>
        </DetailSection>

        {failure && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {failure}
          </Alert>
        )}
        <PluginContentsView
          key={toggled}
          pluginId={plugin.plugin_id}
          revision={plugin.current_revision}
          onToggleSkill={(skill, enabled) => void toggleSkill(skill, enabled)}
        />
      </Box>
    </DetailDialog>
  );
}
