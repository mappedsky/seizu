import { useContext, useEffect, useRef, useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';

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

export function useChatConnections(enabled: boolean) {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [connections, setConnections] = useState<ChatConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    setConnections([]);
    setChecking(null);
    setError(null);
    setLoading(true);
    if (enabled && (!auth_required || accessToken)) {
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      fetch('/api/v1/chat/connections', { headers, signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error('Could not load connections.');
          const data = await response.json();
          if (!controller.signal.aborted) setConnections(data.connections);
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setError('Could not load connections.');
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }
    return () => controller.abort();
  }, [enabled, auth_required, accessToken]);

  const check = async (name: string) => {
    const controller = active.current;
    if (
      !enabled ||
      !controller ||
      controller.signal.aborted ||
      (auth_required && !accessToken)
    )
      return;
    setChecking(name);
    setError(null);
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
        setConnections((items) =>
          items.map((item) => (item.proxy_name === name ? updated : item)),
        );
      }
    } catch {
      if (!controller.signal.aborted)
        setError('Could not check the connection.');
    } finally {
      if (!controller.signal.aborted) setChecking(null);
    }
  };

  return { connections, loading, error, checking, check };
}
