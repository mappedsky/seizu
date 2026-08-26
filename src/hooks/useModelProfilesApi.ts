import { useCallback, useEffect, useState } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

export type ReasoningEffort =
  | 'default'
  | 'none'
  | 'minimal'
  | 'low'
  | 'medium'
  | 'high'
  | 'xhigh';
export type ConfiguredReasoningEffort = ReasoningEffort;

export interface ModelChoice {
  model_id: string;
}

export interface EconomyModelChoice extends ModelChoice {
  reasoning_effort: ConfiguredReasoningEffort;
}

export interface StageModelOverride {
  model_id?: string | null;
  reasoning_effort?: ConfiguredReasoningEffort | null;
}

export interface ModelProfilePayload {
  name: string;
  description: string;
  enabled: boolean;
  is_default: boolean;
  primary: ModelChoice;
  economy: EconomyModelChoice;
  stage_overrides: Record<string, StageModelOverride>;
  user_reasoning_efforts: ReasoningEffort[];
  default_reasoning_effort: ReasoningEffort;
  run_cost_budget_usd: number;
}

export interface ModelProfile extends ModelProfilePayload {
  profile_id: string;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by?: string | null;
}

export interface ModelProfileVersion extends ModelProfilePayload {
  profile_id: string;
  version: number;
  created_at: string;
  created_by: string;
  comment?: string | null;
}

export interface SelectableModelProfile {
  profile_id: string;
  name: string;
  description: string;
  is_default: boolean;
  default_reasoning_effort: ReasoningEffort;
  reasoning_efforts: ReasoningEffort[];
  run_cost_budget_usd: number;
  effective_cost_budget_usd: number;
}

async function responseError(response: Response): Promise<Error> {
  const data = (await response.json().catch(() => null)) as {
    error?: unknown;
    detail?: unknown;
  } | null;
  if (typeof data?.error === 'string') return new Error(data.error);
  if (typeof data?.detail === 'string') return new Error(data.detail);
  if (Array.isArray(data?.detail)) {
    const messages = data.detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null;
        const { loc, msg } = item as { loc?: unknown; msg?: unknown };
        if (typeof msg !== 'string') return null;
        const path = Array.isArray(loc)
          ? loc.filter((part) => part !== 'body').join('.')
          : '';
        return path ? `${path}: ${msg}` : msg;
      })
      .filter((message): message is string => message !== null);
    if (messages.length > 0) return new Error(messages.join('; '));
  }
  return new Error(`Model profile request failed (${response.status})`);
}

export function useSelectableModelProfiles(enabled = true) {
  const { authHeaders } = useAuthHeaders();
  const [profiles, setProfiles] = useState<SelectableModelProfile[]>([]);
  const [defaultProfileId, setDefaultProfileId] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const response = await fetch('/api/v1/chat/model-profiles', {
        headers: authHeaders(),
      });
      if (!response.ok) throw await responseError(response);
      const data = (await response.json()) as {
        profiles: SelectableModelProfile[];
        default_profile_id: string | null;
      };
      setProfiles(data.profiles);
      setDefaultProfileId(data.default_profile_id);
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Failed to load model profiles',
      );
    } finally {
      setLoading(false);
    }
  }, [authHeaders, enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { profiles, defaultProfileId, loading, error, refresh };
}

export function useModelProfilesList(enabled = true) {
  const { authHeaders } = useAuthHeaders();
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [globalRunCostBudgetUsd, setGlobalRunCostBudgetUsd] = useState(0);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const response = await fetch('/api/v1/model-profiles', {
        headers: authHeaders(),
      });
      if (!response.ok) throw await responseError(response);
      const data = (await response.json()) as {
        profiles: ModelProfile[];
        global_run_cost_budget_usd: number;
      };
      setProfiles(data.profiles);
      setGlobalRunCostBudgetUsd(data.global_run_cost_budget_usd);
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Failed to load model profiles',
      );
    } finally {
      setLoading(false);
    }
  }, [authHeaders, enabled]);
  useEffect(() => void refresh(), [refresh]);
  return { profiles, globalRunCostBudgetUsd, loading, error, refresh };
}

export function useModelProfileMutations() {
  const { authHeaders } = useAuthHeaders();
  const request = useCallback(
    async (path: string, method: string, body?: unknown) => {
      const response = await fetch(path, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'X-Seizu-Csrf': '1',
          ...authHeaders(),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) throw await responseError(response);
      return response.json();
    },
    [authHeaders],
  );
  return {
    create: (body: ModelProfilePayload) =>
      request('/api/v1/model-profiles', 'POST', body) as Promise<ModelProfile>,
    update: (
      profileId: string,
      body: ModelProfilePayload & { comment?: string },
    ) =>
      request(
        `/api/v1/model-profiles/${encodeURIComponent(profileId)}`,
        'PUT',
        body,
      ) as Promise<ModelProfile>,
    remove: (profileId: string) =>
      request(
        `/api/v1/model-profiles/${encodeURIComponent(profileId)}`,
        'DELETE',
      ),
    versions: async (profileId: string) => {
      const response = await fetch(
        `/api/v1/model-profiles/${encodeURIComponent(profileId)}/versions`,
        {
          headers: authHeaders(),
        },
      );
      if (!response.ok) throw await responseError(response);
      return ((await response.json()) as { versions: ModelProfileVersion[] })
        .versions;
    },
  };
}
