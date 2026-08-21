import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  Stack,
  Switch,
  Typography,
} from '@mui/material';
import { usePermissions } from 'src/hooks/usePermissions';
import { usePluginMutations, usePluginsList } from 'src/hooks/usePluginsApi';

export default function Plugins() {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const canWrite =
    hasPermission('plugins:write') || hasPermission('skillsets:write');
  const canDelete =
    hasPermission('plugins:delete') || hasPermission('skillsets:delete');
  const { plugins, loading, error, refresh } = usePluginsList();
  const mutations = usePluginMutations();
  const inputRef = useRef<HTMLInputElement>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const upload = async (file?: File) => {
    if (!file) return;
    try {
      await mutations.install(file);
      refresh();
    } catch (reason) {
      setFailure((reason as Error).message);
    }
  };

  return (
    <Container maxWidth="lg">
      <Stack spacing={3}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <Box>
            <Typography variant="h4">Agent Plugins</Typography>
            <Typography color="text.secondary">
              Agent Plugins 1.0.0 packages; each plugin is one Seizu skill
              namespace.
            </Typography>
          </Box>
          <Button
            variant="contained"
            disabled={!canWrite}
            onClick={() => inputRef.current?.click()}
          >
            Install ZIP
          </Button>
          <input
            ref={inputRef}
            hidden
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => void upload(event.target.files?.[0])}
          />
        </Box>
        {(error || failure) && (
          <Alert severity="error">{failure || error?.message}</Alert>
        )}
        {loading && <CircularProgress />}
        {plugins.map((plugin) => (
          <Card key={plugin.plugin_id} variant="outlined">
            <CardContent>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <Typography variant="h6">{plugin.name}</Typography>
                <Chip size="small" label={plugin.plugin_id} />
                <Chip
                  size="small"
                  label={`revision ${plugin.current_revision}`}
                />
              </Stack>
              <Typography color="text.secondary">
                {plugin.description}
              </Typography>
              {plugin.diagnostics.length > 0 && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  {plugin.diagnostics.length} package diagnostic(s)
                </Alert>
              )}
            </CardContent>
            <CardActions>
              <Button
                onClick={async () => {
                  await mutations.createDraft(plugin.plugin_id);
                  navigate(`/app/plugins/${plugin.plugin_id}/edit`);
                }}
                disabled={!canWrite}
              >
                Edit files
              </Button>
              <Switch
                checked={plugin.enabled}
                disabled={!canWrite}
                onChange={async (_, enabled) => {
                  await mutations.setEnabled(plugin.plugin_id, enabled);
                  refresh();
                }}
              />
              <Button
                color="error"
                disabled={!canDelete}
                onClick={async () => {
                  if (window.confirm(`Delete ${plugin.plugin_id}?`)) {
                    await mutations.remove(plugin.plugin_id);
                    refresh();
                  }
                }}
              >
                Delete
              </Button>
            </CardActions>
          </Card>
        ))}
        {!loading && plugins.length === 0 && (
          <Alert severity="info">No plugins installed.</Alert>
        )}
      </Stack>
    </Container>
  );
}
