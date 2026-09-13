import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuthHeaders } from './useAuthHeaders';

export interface ElicitationField {
  type: 'string' | 'number' | 'integer' | 'boolean';
  title?: string;
  description?: string;
  enum?: (string | number | boolean)[];
  enumNames?: string[];
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
}

export interface ChatElicitation {
  elicitation_id: string;
  group_id: string;
  thread_id: string;
  proxy_name: string;
  tool_name: string;
  kind: 'form' | 'url';
  message: string;
  requested_schema?: {
    properties?: Record<string, ElicitationField>;
    required?: string[];
  };
  url?: string;
  status:
    | 'pending'
    | 'accepted'
    | 'declined'
    | 'cancelled'
    | 'expired'
    | 'consumed';
  expires_at: string;
}

export type InputAction = 'accept' | 'decline' | 'cancel';

export function useChatElicitations(threadId: string | null, busy: boolean) {
  const { authHeaders, checkAuthReady } = useAuthHeaders();
  const [items, setItems] = useState<ChatElicitation[]>([]);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const currentThread = useRef(threadId);
  currentThread.current = threadId;
  const refresh = useCallback(async () => {
    if (!threadId || !checkAuthReady()) return;
    try {
      const response = await fetch(
        `/api/v1/chat/elicitations?thread_id=${encodeURIComponent(threadId)}`,
        { headers: authHeaders() },
      );
      if (!response.ok)
        throw new Error('Could not load external input requests.');
      const data = (await response.json()) as {
        elicitations: ChatElicitation[];
      };
      if (!Array.isArray(data.elicitations))
        throw new Error('Invalid input request list.');
      if (currentThread.current === threadId) {
        setItems(data.elicitations);
        setError(null);
      }
    } catch {
      if (currentThread.current === threadId)
        setError('Could not load external input requests.');
    }
  }, [threadId, authHeaders, checkAuthReady]);
  useEffect(() => {
    setItems([]);
    setDismissed([]);
    setError(null);
  }, [threadId]);
  useEffect(() => {
    if (!threadId) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh, busy, threadId]);
  const respond = useCallback(
    async (
      id: string,
      action: InputAction,
      content?: Record<string, unknown>,
    ) => {
      const response = await fetch(
        `/api/v1/chat/elicitations/${encodeURIComponent(id)}/response`,
        {
          method: 'POST',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ action, content }),
        },
      );
      if (!response.ok)
        throw new Error(
          'Could not submit input. Check required fields and whether the request has expired.',
        );
      await refresh();
    },
    [authHeaders, refresh],
  );
  // Answering a request and delivering that answer are separate steps, and the
  // gap between them is a whole turn: the record stays unconsumed until the
  // resumed call claims it, which is what makes recovery possible after an
  // interrupted delivery. Dismissal closes the card over that gap without
  // giving the recovery up -- it is local to this view, so a reload brings an
  // unclaimed card back, and a delivery that fails to dispatch restores it.
  const dismiss = useCallback((id: string) => {
    setDismissed((old) => (old.includes(id) ? old : [...old, id]));
  }, []);
  const restore = useCallback((id: string) => {
    setDismissed((old) => old.filter((value) => value !== id));
  }, []);
  return {
    items: items.filter(
      (item) =>
        item.thread_id === threadId &&
        item.status !== 'consumed' &&
        !dismissed.includes(item.elicitation_id),
    ),
    error,
    respond,
    dismiss,
    restore,
  };
}
