import { useCallback, useEffect, useState } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

export interface ScheduledChatWatchScan {
  grouptype?: string;
  syncedtype?: string;
  groupid?: string;
}

export interface ChatScheduleSpec {
  type: 'hourly' | 'daily' | 'monthly';
  // hourly: run every N hours.
  interval_hours?: number | null;
  // daily: 0=Monday .. 6=Sunday.
  days_of_week?: number[];
  // daily: hour of day (UTC).
  hour?: number;
  // monthly: days 1-31; months without a selected day run on their last day.
  days_of_month?: number[];
}

export interface ScheduledChat {
  scheduled_chat_id: string;
  name: string;
  prompt: string;
  schedule: ChatScheduleSpec | null;
  watch_scans: ScheduledChatWatchScan[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
  last_run_status: string | null;
  last_run_at: string | null;
  last_errors: { timestamp: string; error: string }[];
}

export interface ScheduledChatRequest {
  name: string;
  prompt: string;
  schedule: ChatScheduleSpec | null;
  watch_scans: ScheduledChatWatchScan[];
  enabled: boolean;
}

interface ScheduledChatsResponse {
  schedules: ScheduledChat[];
}

export function useChatSchedules(enabled: boolean): {
  schedules: ScheduledChat[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createSchedule: (req: ScheduledChatRequest) => Promise<void>;
  updateSchedule: (id: string, req: ScheduledChatRequest) => Promise<void>;
  deleteSchedule: (id: string) => Promise<void>;
} {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [schedules, setSchedules] = useState<ScheduledChat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!checkAuthReady()) {
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const res = await fetch('/api/v1/chat/schedules', {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error('Failed to fetch scheduled chats');
      const data = (await res.json()) as ScheduledChatsResponse;
      setSchedules(data.schedules);
    } catch {
      setError('Failed to load scheduled chats.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, checkAuthReady]);

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

  return {
    schedules,
    loading,
    error,
    refresh,
    createSchedule,
    updateSchedule,
    deleteSchedule,
  };
}
