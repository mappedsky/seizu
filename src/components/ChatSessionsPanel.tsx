import { memo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import AddIcon from '@mui/icons-material/Add';
import ChevronLeft from '@mui/icons-material/ChevronLeft';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import Forum from '@mui/icons-material/Forum';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import type { ChatSession } from 'src/hooks/useChatSessions';

const PANEL_WIDTH = 260;

interface ChatSessionsPanelProps {
  open: boolean;
  onToggle: () => void;
  sessions: ChatSession[];
  loading: boolean;
  activeThreadId: string | null;
  onSelectSession: (threadId: string) => void;
  onNewSession: () => void;
  onDeleteSession: (threadId: string) => Promise<void>;
  onRenameSession: (threadId: string, title: string) => Promise<void>;
}

function ChatSessionsPanel({
  open,
  onToggle,
  sessions,
  loading,
  activeThreadId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onRenameSession,
}: ChatSessionsPanelProps) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameThreadId, setRenameThreadId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteThreadId, setDeleteThreadId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const sessionToDelete = sessions.find((s) => s.thread_id === deleteThreadId);

  const handleOpenRename = (session: ChatSession) => {
    setRenameThreadId(session.thread_id);
    setRenameValue(session.title);
    setRenameError(null);
    setRenameOpen(true);
  };

  const handleConfirmRename = async () => {
    if (!renameThreadId || !renameValue.trim()) return;
    setRenaming(true);
    setRenameError(null);
    try {
      await onRenameSession(renameThreadId, renameValue.trim());
      setRenameOpen(false);
    } catch {
      setRenameError('Failed to rename session. Please try again.');
    } finally {
      setRenaming(false);
    }
  };

  const handleOpenDelete = (session: ChatSession) => {
    setDeleteThreadId(session.thread_id);
    setDeleteError(null);
    setDeleteOpen(true);
  };

  const handleConfirmDelete = async () => {
    if (!deleteThreadId) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDeleteSession(deleteThreadId);
      setDeleteOpen(false);
    } catch {
      setDeleteError('Failed to delete session. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (session: ChatSession): RowMenuAction[] => [
    {
      key: 'rename',
      label: 'Rename',
      icon: <EditIcon fontSize="small" />,
      onClick: () => handleOpenRename(session),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => handleOpenDelete(session),
      destructive: true,
      dividerBefore: true,
    },
  ];

  return (
    <>
      <Box
        sx={{
          borderRight: 1,
          borderColor: 'divider',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
          height: '100%',
          overflow: 'hidden',
          transition: 'width 0.2s ease',
          width: open ? PANEL_WIDTH : 40,
        }}
      >
        {/* Header */}
        <Box
          sx={{
            alignItems: 'center',
            borderBottom: 1,
            borderColor: 'divider',
            display: 'flex',
            flexShrink: 0,
            justifyContent: open ? 'space-between' : 'center',
            minHeight: 40,
            px: open ? 1.5 : 0,
          }}
        >
          {open ? (
            <>
              <Typography
                variant="caption"
                sx={{
                  color: 'text.secondary',
                  fontWeight: 700,
                  letterSpacing: 0.8,
                }}
              >
                SESSIONS
              </Typography>
              <Box sx={{ alignItems: 'center', display: 'flex' }}>
                <Tooltip title="New session" placement="right">
                  <IconButton
                    size="small"
                    onClick={onNewSession}
                    aria-label="New session"
                  >
                    <AddIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Collapse" placement="right">
                  <IconButton
                    size="small"
                    onClick={onToggle}
                    aria-label="Collapse sessions panel"
                  >
                    <ChevronLeft fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </>
          ) : (
            <Tooltip title="Sessions" placement="right">
              <IconButton
                size="small"
                onClick={onToggle}
                aria-label="Expand sessions panel"
              >
                <Forum fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </Box>

        {/* Sessions list */}
        {open && (
          <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
            {loading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
                <ConstellationSpinner size={20} />
              </Box>
            ) : sessions.length === 0 ? (
              <Box sx={{ color: 'text.secondary', p: 1.5 }}>
                <Typography variant="caption">No sessions yet.</Typography>
              </Box>
            ) : (
              <List dense disablePadding>
                {sessions.map((session) => (
                  <ListItem
                    key={session.thread_id}
                    disablePadding
                    secondaryAction={
                      <Box sx={{ pr: 0.5 }}>
                        <RowMenu
                          actions={rowActions(session)}
                          menuMinWidth={140}
                        />
                      </Box>
                    }
                  >
                    <ListItemButton
                      selected={session.thread_id === activeThreadId}
                      onClick={() => onSelectSession(session.thread_id)}
                      sx={{ pr: 5 }}
                    >
                      <Typography
                        variant="body2"
                        noWrap
                        title={session.title || 'New session'}
                      >
                        {session.title || 'New session'}
                      </Typography>
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        )}
      </Box>

      {/* Rename dialog */}
      <Dialog
        open={renameOpen}
        onClose={() => setRenameOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Rename session</DialogTitle>
        <DialogContent>
          {renameError ? (
            <Alert severity="error" sx={{ mt: 1 }}>
              {renameError}
            </Alert>
          ) : null}
          <TextField
            autoFocus
            fullWidth
            label="Session name"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleConfirmRename();
            }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameOpen(false)} disabled={renaming}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={!renameValue.trim() || renaming}
            onClick={() => void handleConfirmRename()}
          >
            {renaming ? <ConstellationSpinner size={20} /> : 'Rename'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete confirmation */}
      <ConfirmDeleteDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void handleConfirmDelete()}
        deleting={deleting}
        error={deleteError}
      >
        Delete session{' '}
        <strong>{sessionToDelete?.title || 'New session'}</strong>? This cannot
        be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default memo(ChatSessionsPanel);
