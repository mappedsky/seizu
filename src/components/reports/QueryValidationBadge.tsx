import { useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Tooltip,
  Typography,
} from '@mui/material';
import ErrorOutlineIcon from '@mui/icons-material/Error';
import ErrorIcon from '@mui/icons-material/Error';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import WarningIcon from '@mui/icons-material/Warning';

interface QueryValidationBadgeProps {
  errors: string[];
  warnings: string[];
}

export default function QueryValidationBadge({
  errors,
  warnings,
}: QueryValidationBadgeProps) {
  const [open, setOpen] = useState(false);

  if (errors.length === 0 && warnings.length === 0) return null;

  const hasErrors = errors.length > 0;

  return (
    <>
      <Tooltip title={hasErrors ? 'Query errors' : 'Query warnings'}>
        <IconButton
          size="small"
          color={hasErrors ? 'error' : 'warning'}
          onClick={() => setOpen(true)}
          aria-label={hasErrors ? 'Query errors' : 'Query warnings'}
        >
          {hasErrors ? (
            <ErrorOutlineIcon fontSize="small" />
          ) : (
            <WarningAmberIcon fontSize="small" />
          )}
        </IconButton>
      </Tooltip>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Query Issues</DialogTitle>
        <DialogContent>
          {errors.length > 0 && (
            <>
              <Typography variant="subtitle1" color="error" gutterBottom>
                Errors
              </Typography>
              <List dense disablePadding>
                {errors.map((err, i) => (
                  // eslint-disable-next-line @eslint-react/no-array-index-key
                  <ListItem key={i} disableGutters>
                    <ListItemIcon sx={{ minWidth: 32 }}>
                      <ErrorIcon color="error" fontSize="small" />
                    </ListItemIcon>
                    <ListItemText primary={err} />
                  </ListItem>
                ))}
              </List>
            </>
          )}
          {errors.length > 0 && warnings.length > 0 && (
            <Divider sx={{ my: 1 }} />
          )}
          {warnings.length > 0 && (
            <>
              <Typography variant="subtitle1" color="warning.main" gutterBottom>
                Warnings
              </Typography>
              <List dense disablePadding>
                {warnings.map((w, i) => (
                  // eslint-disable-next-line @eslint-react/no-array-index-key
                  <ListItem key={i} disableGutters>
                    <ListItemIcon sx={{ minWidth: 32 }}>
                      <WarningIcon color="warning" fontSize="small" />
                    </ListItemIcon>
                    <ListItemText primary={w} />
                  </ListItem>
                ))}
              </List>
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
