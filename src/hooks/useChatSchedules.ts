import { useCallback, useEffect, useState } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';
import { ScheduleSpec } from 'src/scheduleSpec';

export interface ScheduledChatWatchScan {
  grouptype?: string;
  syncedtype?: string;
  groupid?: string;
}

// Scheduled chats use the shared ScheduleSpec shape, limited server-side to
// hourly granularity (no 'interval' type, no minute-of-hour).
export type ChatScheduleSpec = ScheduleSpec;

export interface ScheduledChat {
  scheduled_chat_id: string;
  name: string;
  prompt: string;
  schedule: ChatScheduleSpec | null;
  watch_scans: ScheduledChatWatchScan[];
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  last_errors: { timestamp: string; error: string }[];
}

export interface ScheduledChatVersion {
  scheduled_chat_id: string;
  version: number;
  name: string;
  prompt: string;
  schedule: ChatScheduleSpec | null;
  watch_scans: ScheduledChatWatchScan[];
  enabled: boolean;
  created_at: string;
  created_by: string;
  comment: string | null;
}

export interface ScheduledChatSession {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  run_status: string | null;
  run_errors: string[];
}

export interface ScheduledChatRequest {
  name: string;
  prompt: string;
  schedule: ChatScheduleSpec | null;
  watch_scans: ScheduledChatWatchScan[];
  enabled: boolean;
  comment?: string | null;
}

interface ScheduledChatsResponse {
  schedules: ScheduledChat[];
}

export function useChatSchedules(
  enabled: boolean,
  options?: { all?: boolean },
): {
  schedules: ScheduledChat[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createSchedule: (req: ScheduledChatRequest) => Promise<void>;
  updateSchedule: (id: string, req: ScheduledChatRequest) => Promise<void>;
  deleteSchedule: (id: string) => Promise<void>;
  runSchedule: (id: string) => Promise<void>;
} {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [schedules, setSchedules] = useState<ScheduledChat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const all = options?.all === true;

  const refresh = useCallback(async () => {
    if (!checkAuthReady()) {
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const res = await fetch(
        all ? '/api/v1/chat/schedules?all=true' : '/api/v1/chat/schedules',
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error('Failed to fetch scheduled chats');
      const data = (await res.json()) as ScheduledChatsResponse;
      setSchedules(data.schedules);
    } catch {
      setError('Failed to load scheduled chats.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, checkAuthReady, all]);

  useEffect(() => {
    if (enabled) {
      setLoading(true);
      void refresh();
    } else {
      setLoading(false);
    }
  }, [enabled, refresh]);

  const createSchedule = useCallback(
    async (req: ScheduledChatRequest) => {
      const res = await fetch('/api/v1/chat/schedules', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Seizu-Csrf': '1',
          ...authHeaders(),
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) throw new Error('Failed to create scheduled chat');
      await refresh();
    },
    [authHeaders, refresh],
  );

  const updateSchedule = useCallback(
    async (id: string, req: ScheduledChatRequest) => {
      const res = await fetch(
        `/api/v1/chat/schedules/${encodeURIComponent(id)}`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'X-Seizu-Csrf': '1',
            ...authHeaders(),
          },
          body: JSON.stringify(req),
        },
      );
      if (!res.ok) throw new Error('Failed to update scheduled chat');
      await refresh();
    },
    [authHeaders, refresh],
  );

  const deleteSchedule = useCallback(
    async (id: string) => {
      const res = await fetch(
        `/api/v1/chat/schedules/${encodeURIComponent(id)}`,
        {
          method: 'DELETE',
          headers: { 'X-Seizu-Csrf': '1', ...authHeaders() },
        },
      );
      if (!res.ok) throw new Error('Failed to delete scheduled chat');
      await refresh();
    },
    [authHeaders, refresh],
  );

  const runSchedule = useCallback(
    async (id: string) => {
      const res = await fetch(
        `/api/v1/chat/schedules/${encodeURIComponent(id)}/run`,
        {
          method: 'POST',
          headers: { 'X-Seizu-Csrf': '1', ...authHeaders() },
        },
      );
      if (!res.ok) throw new Error('Failed to request scheduled chat run');
    },
    [authHeaders],
  );

  return {
    schedules,
    loading,
    error,
    refresh,
    createSchedule,
    updateSchedule,
    deleteSchedule,
    runSchedule,
  };
}

export function useChatScheduleVersions(scheduleId: string | null): {
  versions: ScheduledChatVersion[];
  loading: boolean;
  error: string | null;
} {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [versions, setVersions] = useState<ScheduledChatVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!scheduleId || !checkAuthReady()) {
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    fetch(`/api/v1/chat/schedules/${encodeURIComponent(scheduleId)}/versions`, {
      headers: authHeaders(),
    })
      .then((res) => {
        if (!res.ok) throw new Error('Failed to fetch versions');
        return res.json() as Promise<{ versions: ScheduledChatVersion[] }>;
      })
      .then((data) => {
        if (!cancelled) setVersions(data.versions);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load version history.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scheduleId, authHeaders, checkAuthReady]);

  return { versions, loading, error };
}

export function useScheduledChatSessions(): (
  scheduleId: string,
) => Promise<ScheduledChatSession[]> {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  return useCallback(
    async (scheduleId: string): Promise<ScheduledChatSession[]> => {
      if (!checkAuthReady()) return [];
      const res = await fetch(
        `/api/v1/chat/schedules/${encodeURIComponent(scheduleId)}/sessions`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error('Failed to fetch run sessions');
      const data = (await res.json()) as { sessions: ScheduledChatSession[] };
      return data.sessions;
    },
    [authHeaders, checkAuthReady],
  );
}

export interface ScheduledChatRunDetail {
  kind: string;
  title: string;
  status?: string;
  arguments?: string;
  body?: string;
}

export interface ScheduledChatTranscriptMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  metadata?: {
    details?: ScheduledChatRunDetail[];
    run_status?: string;
    run_errors?: string[];
  } | null;
}

export function useScheduledChatSessionHistory(): (
  scheduleId: string,
  threadId: string,
) => Promise<ScheduledChatTranscriptMessage[]> {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  return useCallback(
    async (scheduleId: string, threadId: string) => {
      if (!checkAuthReady()) return [];
      const res = await fetch(
        `/api/v1/chat/schedules/${encodeURIComponent(scheduleId)}/sessions/${encodeURIComponent(threadId)}/history`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error('Failed to fetch run transcript');
      const data = (await res.json()) as {
        messages: ScheduledChatTranscriptMessage[];
      };
      return data.messages;
    },
    [authHeaders, checkAuthReady],
  );
}

export function useChatSchedule(scheduleId: string | null): {
  schedule: ScheduledChat | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
} {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [schedule, setSchedule] = useState<ScheduledChat | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!scheduleId || !checkAuthReady()) {
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const res = await fetch(
        `/api/v1/chat/schedules/${encodeURIComponent(scheduleId)}`,
        { headers: authHeaders() },
      );
      if (res.status === 404) throw new Error('not found');
      if (!res.ok) throw new Error('Failed to fetch scheduled chat');
      setSchedule((await res.json()) as ScheduledChat);
    } catch {
      setError('Failed to load scheduled chat.');
    } finally {
      setLoading(false);
    }
  }, [scheduleId, authHeaders, checkAuthReady]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  return { schedule, loading, error, refresh };
}
