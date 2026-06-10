import { useState, useCallback } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

export interface QueryHistoryItem {
  history_id: string;
  user_id: string;
  query: string;
  executed_at: string;
}

export interface QueryHistoryPage {
  items: QueryHistoryItem[];
  total: number;
  page: number;
  per_page: number;
}

interface HistoryState {
  loading: boolean;
  error: Error | null;
  data: QueryHistoryPage | null;
}

export function useQueryHistory() {
  const { authReady, checkAuthReady, authHeaders } = useAuthHeaders();
  const [state, setState] = useState<HistoryState>({
    loading: false,
    error: null,
    data: null,
  });

  const fetchHistory = useCallback(
    (page: number = 1, perPage: number = 20) => {
      if (!checkAuthReady()) return;
      setState((s) => ({ ...s, loading: true, error: null }));

      fetch(`/api/v1/query-history?page=${page}&per_page=${perPage}`, {
        headers: authHeaders(),
      })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data: QueryHistoryPage) => {
          setState({ loading: false, error: null, data });
        })
        .catch((err: Error) => {
          setState({ loading: false, error: err, data: null });
        });
    },
    [authHeaders, checkAuthReady],
  );

  return { ...state, authReady, fetchHistory };
}

export function useFetchHistoryItem(): (
  historyId: string,
) => Promise<QueryHistoryItem | null> {
  const { checkAuthReady, authHeaders } = useAuthHeaders();

  return useCallback(
    async (historyId: string): Promise<QueryHistoryItem | null> => {
      if (!checkAuthReady()) return null;
      try {
        const res = await fetch(`/api/v1/query-history/${historyId}`, {
          headers: authHeaders(),
        });
        if (!res.ok) return null;
        return res.json() as Promise<QueryHistoryItem>;
      } catch {
        return null;
      }
    },
    [authHeaders, checkAuthReady],
  );
}
