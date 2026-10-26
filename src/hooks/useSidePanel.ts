import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useState,
} from 'react';
import { useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';

export interface SidePanelState {
  open: boolean;
  setOpen: Dispatch<SetStateAction<boolean>>;
  toggle: () => void;
  /** True below `md`: the panel floats over the page instead of beside it. */
  overlay: boolean;
  /** Closes the panel when it is an overlay; a no-op beside the page. */
  dismiss: () => void;
}

/**
 * Open state for a collapsible page side panel. Beside the page it follows the
 * caller's own (typically persisted) state. Below `md` there is no room beside
 * the page, so the panel starts closed every visit, opens over the content, and
 * the caller calls `dismiss` once the user has picked something from it.
 */
export function useSidePanel(
  storedOpen: boolean,
  setStoredOpen: Dispatch<SetStateAction<boolean>>,
): SidePanelState {
  const theme = useTheme();
  // noSsr: the first render must already know, or a phone paints the panel
  // open and then snaps it shut.
  const overlay = useMediaQuery(theme.breakpoints.down('md'), { noSsr: true });
  const [overlayOpen, setOverlayOpen] = useState(false);

  const open = overlay ? overlayOpen : storedOpen;
  const setOpen = overlay ? setOverlayOpen : setStoredOpen;
  const toggle = useCallback(() => setOpen((value) => !value), [setOpen]);
  const dismiss = useCallback(() => setOverlayOpen(false), []);

  return { open, setOpen, toggle, overlay, dismiss };
}
