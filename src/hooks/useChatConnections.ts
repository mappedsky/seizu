import { useContext, useEffect, useRef, useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { resourceKey } from 'src/hooks/useAsyncResource';

export type ConnectionStatus =
  | 'unknown'
  | 'connected'
  | 'authorization_required'
  | 'interaction_required'
  | 'service_authentication_failed'
  | 'permission_denied'
  | 'unavailable';

export interface ChatConnection {
  proxy_name: string;
  status: ConnectionStatus;
  observed_at: string | null;
  error_code: ConnectionStatus | null;
  reauthorize_url: string | null;
  elicitations?: {
    elicitation_id: string | null;
    url: string;
    message: string;
  }[];
}

const NO_CONNECTIONS: ChatConnection[] = [];

interface ConnectionsState {
  key: string;
  connections: ChatConnection[];
  error: string | null;
  checking: string | null;
}

export function useChatConnections(enabled: boolean) {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  // The list, the banner and which proxy is being re-checked all belong to one
  // request. Keying them together is what drops a previous identity's answers
  // when the token changes, without an effect clearing four states on entry.
  const [state, setState] = useState<ConnectionsState | null>(null);
  const active = useRef<AbortController | null>(null);
  const requestKey = resourceKey(
    'chat-connections',
    enabled,
    auth_required,
    accessToken,
  );

  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    if (enabled && (!auth_required || accessToken)) {
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      fetch('/api/v1/chat/connections', { headers, signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error('Could not load connections.');
          const data = await response.json();
          if (!controller.signal.aborted)
            setState({
              key: requestKey,
              connections: data.connections,
              error: null,
              checking: null,
            });
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setState({
              key: requestKey,
              connections: NO_CONNECTIONS,
              error: 'Could not load connections.',
              checking: null,
            });
        });
    }
    return () => controller.abort();
  }, [requestKey, enabled, auth_required, accessToken]);

  const current = state !== null && state.key === requestKey ? state : null;

  // Only a settled request can be amended, so a stale response can never
  // reappear as this request's answer.
  const amend = (update: (previous: ConnectionsState) => ConnectionsState) =>
    setState((previous) =>
      previous !== null && previous.key === requestKey
        ? update(previous)
        : previous,
    );

  const check = async (name: string) => {
    const controller = active.current;
    if (
      !enabled ||
      !controller ||
      controller.signal.aborted ||
      (auth_required && !accessToken)
    )
      return;
    amend((previous) => ({ ...previous, checking: name, error: null }));
    const headers: Record<string, string> = { 'X-Seizu-Csrf': '1' };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    try {
      const response = await fetch(
        `/api/v1/chat/connections/${encodeURIComponent(name)}/check`,
        {
          method: 'POST',
          headers,
          signal: controller.signal,
        },
      );
      if (!response.ok) throw new Error('Could not check the connection.');
      const updated: ChatConnection = await response.json();
      if (!controller.signal.aborted) {
        amend((previous) => ({
          ...previous,
          connections: previous.connections.map((item) =>
            item.proxy_name === name ? updated : item,
          ),
          checking: null,
        }));
      }
    } catch {
      if (!controller.signal.aborted)
        amend((previous) => ({
          ...previous,
          error: 'Could not check the connection.',
          checking: null,
        }));
    }
  };

  return {
    connections: current ? current.connections : NO_CONNECTIONS,
    loading: current === null,
    error: current ? current.error : null,
    checking: current ? current.checking : null,
    check,
  };
}
