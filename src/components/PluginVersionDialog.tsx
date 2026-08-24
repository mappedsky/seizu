import { Alert, Box, Stack, Typography } from '@mui/material';
import DetailDialog, {
  DetailCodeBlock,
  DetailSection,
} from 'src/components/DetailDialog';
import PluginContentsView from 'src/components/PluginContentsView';
import UserDisplay from 'src/components/UserDisplay';
import type { PluginVersion } from 'src/hooks/usePluginsApi';

interface Props {
  open: boolean;
  onClose: () => void;
  version: PluginVersion | null;
  isCurrent: boolean;
}

function manifestString(
  version: PluginVersion,
  key: string,
): string | undefined {
  const value = version.manifest?.[key];
  return typeof value === 'string' && value ? value : undefined;
}

/**
 * What a past revision actually contains.
 *
 * The history list can only show when a revision was saved and by whom, which
 * is not enough to decide whether to restore it — so this shows the same
 * structure the current-revision dialog does, for the revision in question.
 */
export default function PluginVersionDialog({
  open,
  onClose,
  version,
  isCurrent,
}: Props) {
  if (!version) return null;

  return (
    <DetailDialog
      open={open}
      onClose={onClose}
      title={manifestString(version, 'name') ?? version.plugin_id}
      secondary={`v${version.revision}${isCurrent ? ' · current' : ''}`}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {!isCurrent && (
          <Alert severity="info" sx={{ mb: 2 }}>
            This is an earlier revision. Restoring it republishes exactly these
            files as a new revision.
          </Alert>
        )}

        <DetailSection title="Namespace">
          <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
            {version.plugin_id}
          </Typography>
        </DetailSection>

        <DetailSection title="Package version">
          <Typography variant="body2">
            {manifestString(version, 'version') ?? '—'}
          </Typography>
        </DetailSection>

        {manifestString(version, 'description') && (
          <DetailSection title="Description">
            <Typography variant="body2">
              {manifestString(version, 'description')}
            </Typography>
          </DetailSection>
        )}

        <DetailSection title="Saved">
          <Typography variant="body2">
            {new Date(version.created_at).toLocaleString()} by{' '}
            <UserDisplay userId={version.created_by} />
          </Typography>
        </DetailSection>

        {version.comment && (
          <DetailSection title="Comment">
            <Typography variant="body2">{version.comment}</Typography>
          </DetailSection>
        )}

        <DetailSection title="Package digest">
          <DetailCodeBlock>{version.package_digest || '—'}</DetailCodeBlock>
        </DetailSection>

        {version.diagnostics.length > 0 && (
          <DetailSection title="Diagnostics">
            <Stack spacing={1}>
              {version.diagnostics.map((diagnostic) => (
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

        <PluginContentsView
          pluginId={version.plugin_id}
          revision={version.revision}
        />
      </Box>
    </DetailDialog>
  );
}
