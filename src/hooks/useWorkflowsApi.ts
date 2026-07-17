import { useCallback, useContext, useEffect, useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { ScheduleSpec } from 'src/scheduleSpec';

export interface WorkflowParameter {
  name: string;
  value: unknown;
}

export interface WorkflowActivity {
  type: string;
  input: string | null;
  output: string;
  parameters: Record<string, unknown>;
}

export interface WorkflowStage {
  activities: WorkflowActivity[];
}

export interface WorkflowWatchScan {
  grouptype?: string;
  syncedtype?: string;
  groupid?: string;
}

export interface WorkflowItem {
  workflow_id: string;
  name: string;
  stages: WorkflowStage[];
  schedule: ScheduleSpec | null;
  watch_scans: WorkflowWatchScan[];
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  last_errors: { timestamp: string; error: string }[];
  schedule_sync_status: 'synced' | 'pending' | 'error';
  schedule_sync_error: string | null;
  schedule_synced_at: string | null;
}

export interface WorkflowRequest {
  name: string;
  stages: WorkflowStage[];
  schedule: ScheduleSpec | null;
  watch_scans: WorkflowWatchScan[];
  enabled: boolean;
  comment?: string | null;
}

function headers(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const data = (await response.json()) as { error?: string; detail?: string };
    return new Error(data.error ?? data.detail ?? fallback);
  } catch {
    return new Error(fallback);
  }
}

export function useWorkflowsList(): {
  workflows: WorkflowItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (auth_required && !accessToken) return;
    setLoading(true);
    setError(null);
    fetch('/api/v1/workflows', { headers: headers(accessToken) })
      .then(async (response) => {
        if (!response.ok)
          throw await apiError(response, 'Failed to load workflows.');
        return response.json() as Promise<{ workflows: WorkflowItem[] }>;
      })
      .then((data) => setWorkflows(data.workflows ?? []))
      .catch((reason: Error) => setError(reason))
      .finally(() => setLoading(false));
  }, [accessToken, auth_required, tick]);

  return { workflows, loading, error, refresh };
}

export function useWorkflow(id: string | null): {
  workflow: WorkflowItem | null;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [workflow, setWorkflow] = useState<WorkflowItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((value) => value + 1), []);
  useEffect(() => {
    if (!id || (auth_required && !accessToken)) return;
    setLoading(true);
    fetch(`/api/v1/workflows/${encodeURIComponent(id)}`, {
      headers: headers(accessToken),
    })
      .then(async (response) => {
        if (!response.ok)
          throw await apiError(response, 'Failed to load workflow.');
        return response.json() as Promise<WorkflowItem>;
      })
      .then(setWorkflow)
      .catch((reason: Error) => setError(reason))
      .finally(() => setLoading(false));
  }, [accessToken, auth_required, id, tick]);
  return { workflow, loading, error, refresh };
}

export function useWorkflowMutations(): {
  createWorkflow: (request: WorkflowRequest) => Promise<WorkflowItem>;
  updateWorkflow: (
    id: string,
    request: WorkflowRequest,
  ) => Promise<WorkflowItem>;
  deleteWorkflow: (id: string) => Promise<void>;
  runWorkflow: (id: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);
  const mutate = useCallback(
    async (
      url: string,
      method: string,
      body?: WorkflowRequest,
    ): Promise<Response> => {
      const response = await fetch(url, {
        method,
        headers: {
          ...headers(accessToken),
          ...(body ? { 'Content-Type': 'application/json' } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok)
        throw await apiError(response, 'Workflow request failed.');
      return response;
    },
    [accessToken],
  );
  return {
    createWorkflow: async (request) =>
      (await mutate('/api/v1/workflows', 'POST', request)).json(),
    updateWorkflow: async (id, request) =>
      (
        await mutate(
          `/api/v1/workflows/${encodeURIComponent(id)}`,
          'PUT',
          request,
        )
      ).json(),
    deleteWorkflow: async (id) => {
      await mutate(`/api/v1/workflows/${encodeURIComponent(id)}`, 'DELETE');
    },
    runWorkflow: async (id) => {
      await mutate(`/api/v1/workflows/${encodeURIComponent(id)}/run`, 'POST');
    },
  };
}
