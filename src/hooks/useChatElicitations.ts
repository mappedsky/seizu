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

const NO_ELICITATIONS: ChatElicitation[] = [];
const NO_DISMISSED: string[] = [];

export function useChatElicitations(threadId: string | null, busy: boolean) {
  const { authHeaders, checkAuthReady } = useAuthHeaders();
  // Both pieces of per-thread state carry the thread they belong to, so
  // switching threads drops the previous one's cards and its error by being
  // read past rather than by an effect that clears them a render later.
  const [loaded, setLoaded] = useState<{
    threadId: string;
    items: ChatElicitation[];
    error: string | null;
  } | null>(null);
  const [dismissed, setDismissed] = useState<{
    threadId: string | null;
    ids: string[];
  }>({ threadId: null, ids: NO_DISMISSED });
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
        setLoaded({ threadId, items: data.elicitations, error: null });
      }
    } catch {
      if (currentThread.current === threadId)
        setLoaded((current) => ({
          threadId,
          items:
            current !== null && current.threadId === threadId
              ? current.items
              : NO_ELICITATIONS,
          error: 'Could not load external input requests.',
        }));
    }
  }, [threadId, authHeaders, checkAuthReady]);
  const current =
    loaded !== null && loaded.threadId === threadId ? loaded : null;
  const items = current ? current.items : NO_ELICITATIONS;
  const error = current ? current.error : null;
  const dismissedIds =
    dismissed.threadId === threadId ? dismissed.ids : NO_DISMISSED;
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
  const dismiss = useCallback(
    (id: string) => {
      setDismissed((old) => {
        if (old.threadId !== threadId) return { threadId, ids: [id] };
        return old.ids.includes(id) ? old : { threadId, ids: [...old.ids, id] };
      });
    },
    [threadId],
  );
  const restore = useCallback(
    (id: string) => {
      setDismissed((old) =>
        old.threadId === threadId
          ? { threadId, ids: old.ids.filter((value) => value !== id) }
          : old,
      );
    },
    [threadId],
  );
  return {
    items: items.filter(
      (item) =>
        item.thread_id === threadId &&
        item.status !== 'consumed' &&
        !dismissedIds.includes(item.elicitation_id),
    ),
    error,
    respond,
    dismiss,
    restore,
  };
}
