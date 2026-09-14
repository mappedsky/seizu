import { useState, useContext, useCallback } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { resourceKey, useAsyncResource } from 'src/hooks/useAsyncResource';
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

export interface WorkflowRunSummary {
  workflow_id: string;
  run_id: string;
  workflow_name: string;
  status: string;
  start_time: string | null;
  close_time: string | null;
  history_length: number | null;
}

export interface WorkflowRunActivity {
  activity_id: string;
  activity_type: string;
  status: string;
  attempts: number;
  maximum_attempts: number | null;
  scheduled_at: string | null;
  started_at: string | null;
  closed_at: string | null;
  retry_state: string | null;
  failure: string | null;
  last_attempt_failure: string | null;
  input_preview: string | null;
  result_preview: string | null;
}

export interface WorkflowRunDetail {
  workflow_id: string;
  run_id: string;
  workflow_name: string;
  status: string;
  start_time: string | null;
  close_time: string | null;
  failure: string | null;
  activities: WorkflowRunActivity[];
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
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const {
    data: scheduledQueries,
    loading,
    error,
  } = useAsyncResource<ScheduledQueryItem[]>(
    resourceKey('scheduled-queries', auth_required, accessToken, tick),
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/scheduled-queries', {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load scheduled queries: ${res.status}`);
          const data: { scheduled_queries: ScheduledQueryItem[] } =
            await res.json();
          return data.scheduled_queries ?? [];
        },
    [],
  );

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
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const {
    data: query,
    loading,
    error,
  } = useAsyncResource<ScheduledQueryItem | null>(
    resourceKey('scheduled-query', id, auth_required, accessToken, tick),
    !id || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(`/api/v1/scheduled-queries/${id}`, {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load scheduled query: ${res.status}`);
          return (await res.json()) as ScheduledQueryItem;
        },
    null,
  );

  return { query, loading, error, refresh };
}

export function useScheduledQueryVersionsList(sqId: string | null): {
  versions: ScheduledQueryVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const {
    data: versions,
    loading,
    error,
  } = useAsyncResource<ScheduledQueryVersion[]>(
    resourceKey('scheduled-query-versions', sqId, auth_required, accessToken),
    !sqId || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(
            `/api/v1/scheduled-queries/${sqId}/versions`,
            {
              headers: getApiHeaders(accessToken),
            },
          );
          if (!res.ok)
            throw new Error(
              `Failed to load scheduled query versions: ${res.status}`,
            );
          const data: { versions: ScheduledQueryVersion[] } = await res.json();
          return data.versions ?? [];
        },
    [],
  );

  return { versions, loading, error };
}

export function useScheduledQueryWorkflowRuns(
  id: string | null,
  enabled: boolean,
): {
  runs: WorkflowRunSummary[] | null;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const active = Boolean(id) && enabled;
  const { data, loading, error } = useAsyncResource<WorkflowRunSummary[]>(
    active
      ? resourceKey('workflow-runs', id, auth_required, accessToken)
      : null,
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch(
            `/api/v1/scheduled-queries/${encodeURIComponent(id as string)}/workflow-runs`,
            { headers: getApiHeaders(accessToken) },
          );
          if (!res.ok)
            throw new Error(`Failed to load workflow runs: ${res.status}`);
          const body: { runs: WorkflowRunSummary[] } = await res.json();
          return body.runs ?? [];
        },
    [],
  );

  // `null` is this hook's "no answer yet", and it must not survive into the
  // next question: remounting onto a different query showed the previous
  // query's runs while the new fetch was in flight.
  return { runs: active && !loading ? data : null, error };
}

export function useWorkflowRunDetail(): (
  sqId: string,
  workflowId: string,
  runId: string,
) => Promise<WorkflowRunDetail> {
  const { accessToken } = useContext(AuthContext);

  return useCallback(
    async (
      sqId: string,
      workflowId: string,
      runId: string,
    ): Promise<WorkflowRunDetail> => {
      const res = await fetch(
        `/api/v1/scheduled-queries/${encodeURIComponent(sqId)}/workflow-runs/${encodeURIComponent(workflowId)}/${encodeURIComponent(runId)}`,
        { headers: getApiHeaders(accessToken) },
      );
      if (!res.ok)
        throw new Error(`Failed to load workflow run: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );
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
