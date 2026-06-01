import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

export interface ChatSession {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface ChatSessionsResponse {
  sessions: ChatSession[];
}

function sortSessions(sessions: ChatSession[]): ChatSession[] {
  return [...sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

function upsertAndSortSession(
  sessions: ChatSession[],
  session: ChatSession,
): ChatSession[] {
  return sortSessions([
    session,
    ...sessions.filter((s) => s.thread_id !== session.thread_id),
  ]);
}

export function useChatSessions(enabled: boolean): {
  sessions: ChatSession[];
  loading: boolean;
  error: string | null;
  createSession: (title?: string) => Promise<ChatSession>;
  getSession: (threadId: string) => Promise<ChatSession | null>;
  updateSession: (threadId: string, title: string) => Promise<void>;
  deleteSession: (threadId: string) => Promise<void>;
  touchSession: (threadId: string) => void;
} {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSessions = useCallback(async () => {
    if (!checkAuthReady()) {
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const res = await fetch('/api/v1/chat/sessions?limit=100', {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error('Failed to fetch sessions');
      const data = (await res.json()) as ChatSessionsResponse;
      // The list endpoint is already ordered by updated_at DESC. Local mutations
      // still sort because optimistic updates can change ordering client-side.
      setSessions(data.sessions);
    } catch {
      setError('Failed to load chat sessions.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, checkAuthReady]);

  useEffect(() => {
    if (enabled) {
      setLoading(true);
      void fetchSessions();
      const handleFocus = () => {
        void fetchSessions();
      };
      window.addEventListener('focus', handleFocus);
      return () => window.removeEventListener('focus', handleFocus);
    } else {
      setLoading(false);
    }
    return undefined;
  }, [enabled, fetchSessions]);

  const createSession = useCallback(
    async (title = ''): Promise<ChatSession> => {
      const res = await fetch('/api/v1/chat/sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Seizu-Csrf': '1',
          ...authHeaders(),
        },
        body: JSON.stringify({ title }),
      });
      if (!res.ok) throw new Error('Failed to create session');
      const session = (await res.json()) as ChatSession;
      setSessions((prev) => upsertAndSortSession(prev, session));
      return session;
    },
    [authHeaders],
  );

  const getSession = useCallback(
    async (threadId: string): Promise<ChatSession | null> => {
      const res = await fetch(
        `/api/v1/chat/sessions/${encodeURIComponent(threadId)}`,
        { headers: authHeaders() },
      );
      if (res.status === 404) return null;
      if (!res.ok) throw new Error('Failed to fetch session');
      const session = (await res.json()) as ChatSession;
      setSessions((prev) => upsertAndSortSession(prev, session));
      return session;
    },
    [authHeaders],
  );

  const updateSession = useCallback(
    async (threadId: string, title: string): Promise<void> => {
      const optimisticUpdatedAt = new Date().toISOString();
      const previousSession = sessionsRef.current.find(
        (s) => s.thread_id === threadId,
      );
      setSessions((prev) =>
        sortSessions(
          prev.map((s) =>
            s.thread_id === threadId
              ? { ...s, title, updated_at: optimisticUpdatedAt }
              : s,
          ),
        ),
      );
      try {
        const res = await fetch(
          `/api/v1/chat/sessions/${encodeURIComponent(threadId)}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-Seizu-Csrf': '1',
              ...authHeaders(),
            },
            body: JSON.stringify({ title }),
          },
        );
        if (!res.ok) throw new Error('Failed to update session');
        const updated = (await res.json()) as ChatSession;
        setSessions((prev) =>
          sortSessions(
            prev.map((s) => (s.thread_id === threadId ? updated : s)),
          ),
        );
      } catch (err) {
        if (previousSession) {
          setSessions((prev) =>
            sortSessions(
              prev.map((s) => (s.thread_id === threadId ? previousSession : s)),
            ),
          );
        }
        throw err;
      }
    },
    [authHeaders],
  );

  const deleteSession = useCallback(
    async (threadId: string): Promise<void> => {
      const res = await fetch(
        `/api/v1/chat/sessions/${encodeURIComponent(threadId)}`,
        {
          method: 'DELETE',
          headers: { 'X-Seizu-Csrf': '1', ...authHeaders() },
        },
      );
      if (!res.ok && res.status !== 404)
        throw new Error('Failed to delete session');
      setSessions((prev) => prev.filter((s) => s.thread_id !== threadId));
    },
    [authHeaders],
  );

  const touchSession = useCallback((threadId: string): void => {
    const now = new Date().toISOString();
    setSessions((prev) =>
      sortSessions(
        prev.map((s) =>
          s.thread_id === threadId ? { ...s, updated_at: now } : s,
        ),
      ),
    );
  }, []);

  return {
    sessions,
    loading,
    error,
    createSession,
    getSession,
    updateSession,
    deleteSession,
    touchSession,
  };
}
