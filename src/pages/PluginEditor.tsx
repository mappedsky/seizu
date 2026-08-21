import { ChangeEvent, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Container,
  Divider,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import {
  PluginDiagnostic,
  PluginFileInfo,
  usePluginMutations,
} from 'src/hooks/usePluginsApi';

export default function PluginEditor() {
  const { pluginId = '' } = useParams();
  const navigate = useNavigate();
  const api = usePluginMutations();
  const [files, setFiles] = useState<PluginFileInfo[]>([]);
  const [selected, setSelected] = useState<PluginFileInfo | null>(null);
  const [text, setText] = useState('');
  const [binary, setBinary] = useState(false);
  const [diagnostics, setDiagnostics] = useState<PluginDiagnostic[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const reload = async () => setFiles(await api.listDraftFiles(pluginId));
  useEffect(() => {
    void reload();
  }, [pluginId]);

  const choose = async (file: PluginFileInfo) => {
    setSelected(file);
    const body = await api.readDraftFile(pluginId, file.path);
    try {
      setText(new TextDecoder('utf-8', { fatal: true }).decode(body.bytes));
      setBinary(false);
    } catch {
      setText('');
      setBinary(true);
    }
  };
  const save = async () => {
    if (!selected || binary) return;
    const updated = await api.writeDraftFile(
      pluginId,
      selected.path,
      new TextEncoder().encode(text),
      selected.media_type,
      selected.etag,
      selected.executable,
    );
    setSelected(updated);
    setMessage(`Saved ${selected.path}`);
    await reload();
  };
  const remove = async () => {
    if (!selected || !window.confirm(`Delete ${selected.path} from the draft?`))
      return;
    await api.deleteDraftFile(pluginId, selected.path, selected.etag);
    setSelected(null);
    setText('');
    await reload();
  };
  const addFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const path = window.prompt('Plugin-relative path', file.name);
    if (!path) return;
    const existing = files.find((item) => item.path === path);
    await api.writeDraftFile(
      pluginId,
      path,
      new Uint8Array(await file.arrayBuffer()),
      file.type || 'application/octet-stream',
      existing?.etag,
      existing?.executable,
    );
    await reload();
  };
  const validate = async () => {
    const result = await api.validateDraft(pluginId);
    setDiagnostics(result.diagnostics);
    setMessage(result.valid ? 'Draft is valid.' : 'Draft has blocking errors.');
  };
  const publish = async () => {
    const result = await api.validateDraft(pluginId);
    setDiagnostics(result.diagnostics);
    if (!result.valid) return;
    await api.publishDraft(pluginId);
    navigate('/app/plugins');
  };
  const discard = async () => {
    if (!window.confirm('Discard every unpublished change in this draft?'))
      return;
    await api.discardDraft(pluginId);
    navigate('/app/plugins');
  };

  return (
    <Container maxWidth={false}>
      <Stack spacing={2}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
          <Box>
            <Typography variant="h4">Edit {pluginId}</Typography>
            <Typography color="text.secondary">
              Changes stay in a draft until the package validates and is
              published atomically.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button component="label">
              Add file
              <input
                hidden
                type="file"
                onChange={(event) => void addFile(event)}
              />
            </Button>
            <Button onClick={() => void validate()}>Validate</Button>
            <Button color="error" onClick={() => void discard()}>
              Discard draft
            </Button>
            <Button variant="contained" onClick={() => void publish()}>
              Publish
            </Button>
          </Stack>
        </Box>
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
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: 'minmax(240px, 28%) 1fr',
            minHeight: '65vh',
            border: 1,
            borderColor: 'divider',
          }}
        >
          <List sx={{ overflow: 'auto' }}>
            {files.map((file) => (
              <ListItemButton
                key={file.path}
                selected={selected?.path === file.path}
                onClick={() => void choose(file)}
              >
                <ListItemText
                  primary={file.path}
                  secondary={`${file.media_type} · ${file.size} bytes`}
                />
              </ListItemButton>
            ))}
          </List>
          <Box sx={{ borderLeft: 1, borderColor: 'divider', p: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
              <Typography variant="subtitle2">
                {selected?.path || 'Choose a file'}
              </Typography>
              <Button
                size="small"
                color="error"
                disabled={!selected}
                onClick={() => void remove()}
              >
                Delete
              </Button>
            </Box>
            <Divider sx={{ my: 1 }} />
            {binary ? (
              <Alert severity="info">
                Binary file. Replace it with Add file using the same path.
              </Alert>
            ) : (
              <>
                <TextField
                  fullWidth
                  multiline
                  minRows={24}
                  value={text}
                  disabled={!selected}
                  onChange={(event) => setText(event.target.value)}
                  slotProps={{
                    htmlInput: {
                      spellCheck: false,
                      style: { fontFamily: 'monospace' },
                    },
                  }}
                />
                <Button
                  sx={{ mt: 1 }}
                  variant="contained"
                  disabled={!selected}
                  onClick={() => void save()}
                >
                  Save file
                </Button>
              </>
            )}
          </Box>
        </Box>
      </Stack>
    </Container>
  );
}
