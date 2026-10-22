import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Tooltip,
} from '@mui/material';
import GppMaybe from '@mui/icons-material/GppMaybe';

/**
 * Asked the first time bypass is turned on in a visit: what it changes, that
 * it is logged, and that it ends with the visit. Cancel is the default.
 */
export function BypassConfirmDialog({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      maxWidth="xs"
      fullWidth
      aria-labelledby="bypass-confirm-title"
    >
      <DialogTitle id="bypass-confirm-title">
        Run actions without asking?
      </DialogTitle>
      <DialogContent>
        <DialogContentText>
          While bypass is on, actions the assistant takes for you — creating,
          changing or deleting reports, workflows and other records — run
          without waiting for your approval. Each one is audit-logged.
        </DialogContentText>
        <DialogContentText sx={{ mt: 1.5 }}>
          Bypass stays on until you turn it off or leave this page.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} autoFocus>
          Keep asking
        </Button>
        <Button onClick={onConfirm} color="warning" variant="contained">
          Turn on bypass
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/**
 * Sits in the composer while bypass is on, beside the model it applies to,
 * so the state is in view at the moment a message is sent.
 */
export function BypassChip({ onTurnOff }: { onTurnOff: () => void }) {
  return (
    <Tooltip title="Actions run without asking for approval. Click to turn off.">
      <Chip
        color="warning"
        icon={<GppMaybe />}
        label="Bypass on"
        // The whole chip turns bypass off, by click or Enter, as well as its
        // delete icon by pointer or Backspace.
        onClick={onTurnOff}
        onDelete={onTurnOff}
        aria-label="Bypass on. Turn off bypass"
        size="small"
        variant="outlined"
        sx={{ flexShrink: 0, fontWeight: 600 }}
      />
    </Tooltip>
  );
}
