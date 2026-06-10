import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
  useState,
} from 'react';

const ACTIVE_SESSION_KEY = 'seizu:chat:active-session';
const SESSIONS_PANEL_KEY = 'seizu:chat:sessions-panel-open';

function getInitialPanelOpen(): boolean {
  if (typeof window === 'undefined') return true;
  const stored = window.localStorage.getItem(SESSIONS_PANEL_KEY);
  return stored !== 'false';
}

export function useChatLocalStorage(): {
  getStoredActiveSessionId: () => string | null;
  panelOpen: boolean;
  setPanelOpen: Dispatch<SetStateAction<boolean>>;
  setStoredActiveSessionId: (threadId: string) => void;
} {
  const [panelOpen, setPanelOpen] = useState(getInitialPanelOpen);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(SESSIONS_PANEL_KEY, String(panelOpen));
    }
  }, [panelOpen]);

  const getStoredActiveSessionId = useCallback(() => {
    if (typeof window === 'undefined') return null;
    return window.localStorage.getItem(ACTIVE_SESSION_KEY);
  }, []);

  const setStoredActiveSessionId = useCallback((threadId: string) => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(ACTIVE_SESSION_KEY, threadId);
    }
  }, []);

  return {
    getStoredActiveSessionId,
    panelOpen,
    setPanelOpen,
    setStoredActiveSessionId,
  };
}
