import { useState, useEffect, useContext, useCallback } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { ScheduleSpec } from 'src/scheduleSpec';

export interface ScheduledQueryParam {
  name: string;
  value: unknown;
}

export interface ScheduledQueryWatchScan {
  grouptype?: string;
  syncedtype?: string;
  groupid?: string;
}

export interface ScheduledQueryAction {
  action_type: string;
  action_config: Record<string, unknown>;
}

export interface ScheduledQueryRunError {
  timestamp: string;
  error: string;
}

export interface ScheduledQueryItem {
  scheduled_query_id: string;
  name: string;
  cypher: string;
  params: ScheduledQueryParam[];
  // Deprecated: interval in minutes; superseded by schedule.
  frequency: number | null;
  schedule: ScheduleSpec | null;
  watch_scans: ScheduledQueryWatchScan[];
  enabled: boolean;
  actions: ScheduledQueryAction[];
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  last_errors: ScheduledQueryRunError[];
}

export interface ScheduledQueryVersion {
  scheduled_query_id: string;
  name: string;
  version: number;
  cypher: string;
  params: ScheduledQueryParam[];
  frequency: number | null;
  schedule: ScheduleSpec | null;
  watch_scans: ScheduledQueryWatchScan[];
  enabled: boolean;
  actions: ScheduledQueryAction[];
  created_at: string;
  created_by: string;
  comment: string | null;
}

export interface ScheduledQueryRequest {
  name: string;
  cypher: string;
  params: ScheduledQueryParam[];
  // Deprecated: interval in minutes; superseded by schedule.
  frequency: number | null;
  schedule: ScheduleSpec | null;
  watch_scans: ScheduledQueryWatchScan[];
  enabled: boolean;
  actions: ScheduledQueryAction[];
  comment?: string | null;
}

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

export function useScheduledQueriesList(): {
  scheduledQueries: ScheduledQueryItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [scheduledQueries, setScheduledQueries] = useState<
    ScheduledQueryItem[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch('/api/v1/scheduled-queries', { headers: getApiHeaders(accessToken) })
      .then((res) => {
        if (!res.ok)
          throw new Error(`Failed to load scheduled queries: ${res.status}`);
        return res.json();
      })
      .then((data: { scheduled_queries: ScheduledQueryItem[] }) => {
        setScheduledQueries(data.scheduled_queries ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [accessToken, auth_required, tick]);

  return { scheduledQueries, loading, error, refresh };
}

export function useScheduledQuery(id: string | null): {
  query: ScheduledQueryItem | null;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [query, setQuery] = useState<ScheduledQueryItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!id) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch(`/api/v1/scheduled-queries/${id}`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok)
          throw new Error(`Failed to load scheduled query: ${res.status}`);
        return res.json();
      })
      .then((data: ScheduledQueryItem) => {
        setQuery(data);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [id, accessToken, auth_required, tick]);

  return { query, loading, error, refresh };
}

export function useScheduledQueryVersionsList(sqId: string | null): {
  versions: ScheduledQueryVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [versions, setVersions] = useState<ScheduledQueryVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!sqId) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch(`/api/v1/scheduled-queries/${sqId}/versions`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok)
          throw new Error(
            `Failed to load scheduled query versions: ${res.status}`,
          );
        return res.json();
      })
      .then((data: { versions: ScheduledQueryVersion[] }) => {
        setVersions(data.versions ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [sqId, accessToken, auth_required]);

  return { versions, loading, error };
}

export function useScheduledQueriesMutations(): {
  createScheduledQuery: (
    req: ScheduledQueryRequest,
  ) => Promise<ScheduledQueryItem>;
  updateScheduledQuery: (
    id: string,
    req: ScheduledQueryRequest,
  ) => Promise<ScheduledQueryItem>;
  deleteScheduledQuery: (id: string) => Promise<void>;
  runScheduledQuery: (id: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);

  const createScheduledQuery = useCallback(
    async (req: ScheduledQueryRequest): Promise<ScheduledQueryItem> => {
      const res = await fetch('/api/v1/scheduled-queries', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok)
        throw new Error(`Failed to create scheduled query: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const updateScheduledQuery = useCallback(
    async (
      id: string,
      req: ScheduledQueryRequest,
    ): Promise<ScheduledQueryItem> => {
      const res = await fetch(`/api/v1/scheduled-queries/${id}`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok)
        throw new Error(`Failed to update scheduled query: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const deleteScheduledQuery = useCallback(
    async (id: string): Promise<void> => {
      const res = await fetch(`/api/v1/scheduled-queries/${id}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok)
        throw new Error(`Failed to delete scheduled query: ${res.status}`);
    },
    [accessToken],
  );

  const runScheduledQuery = useCallback(
    async (id: string): Promise<void> => {
      const res = await fetch(`/api/v1/scheduled-queries/${id}/run`, {
        method: 'POST',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok)
        throw new Error(`Failed to request scheduled query run: ${res.status}`);
    },
    [accessToken],
  );

  return {
    createScheduledQuery,
    updateScheduledQuery,
    deleteScheduledQuery,
    runScheduledQuery,
  };
}
