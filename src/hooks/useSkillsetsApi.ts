import { useState, useContext, useCallback } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { resourceKey, useAsyncResource } from 'src/hooks/useAsyncResource';
import { ToolParamDef } from 'src/hooks/useToolsetsApi';

export interface SkillsetListItem {
  skillset_id: string;
  name: string;
  description: string;
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
}

export interface SkillsetVersion {
  skillset_id: string;
  name: string;
  description: string;
  enabled: boolean;
  version: number;
  created_at: string;
  created_by: string;
  comment: string | null;
}

/**
 * The fully-qualified MCP slug for a skill — `skillset__skill`. This is the exact
 * name the skill is callable/renderable by (in chat, the MCP server, the CLI).
 */
export function mcpNameForSkill(skill: {
  skill_id: string;
  skillset_id: string;
}): string {
  return `${skill.skillset_id}__${skill.skill_id}`;
}

export interface SkillItem {
  skill_id: string;
  skillset_id: string;
  name: string;
  description: string;
  template: string;
  parameters: ToolParamDef[];
  triggers?: string[];
  tools_required?: string[];
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
  effective_enabled?: boolean | null;
  disabled_reason?: string | null;
}

export interface SkillVersion {
  skill_id: string;
  skillset_id: string;
  name: string;
  description: string;
  template: string;
  parameters: ToolParamDef[];
  triggers: string[];
  tools_required: string[];
  enabled: boolean;
  version: number;
  created_at: string;
  created_by: string;
  comment: string | null;
}

export interface CreateSkillsetRequest {
  skillset_id: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface UpdateSkillsetRequest {
  name: string;
  description: string;
  enabled: boolean;
  comment?: string | null;
}

export interface CreateSkillRequest {
  skill_id: string;
  name: string;
  description: string;
  template: string;
  parameters: ToolParamDef[];
  triggers: string[];
  tools_required: string[];
  enabled: boolean;
}

export interface UpdateSkillRequest {
  name: string;
  description: string;
  template: string;
  parameters: ToolParamDef[];
  triggers: string[];
  tools_required: string[];
  enabled: boolean;
  comment?: string | null;
}

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return headers;
}

async function errorMessage(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return (
    (data as { errors?: string[]; detail?: string }).errors?.join(', ') ??
    (data as { detail?: string }).detail ??
    fallback
  );
}

export function useSkillsetsList(): {
  skillsets: SkillsetListItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const {
    data: skillsets,
    loading,
    error,
  } = useAsyncResource<SkillsetListItem[]>(
    resourceKey('skillsets', auth_required, accessToken, tick),
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/skillsets', {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load skillsets: ${res.status}`);
          const data: { skillsets: SkillsetListItem[] } = await res.json();
          return data.skillsets ?? [];
        },
    [],
  );

  return { skillsets, loading, error, refresh };
}

export function useSkillsetVersionsList(skillsetId: string | null): {
  versions: SkillsetVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const {
    data: versions,
    loading,
    error,
  } = useAsyncResource<SkillsetVersion[]>(
    resourceKey('skillset-versions', skillsetId, auth_required, accessToken),
    !skillsetId || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(`/api/v1/skillsets/${skillsetId}/versions`, {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load skillset versions: ${res.status}`);
          const data: { versions: SkillsetVersion[] } = await res.json();
          return data.versions ?? [];
        },
    [],
  );

  return { versions, loading, error };
}

export function useSkillsetMutations(): {
  createSkillset: (req: CreateSkillsetRequest) => Promise<SkillsetListItem>;
  updateSkillset: (
    id: string,
    req: UpdateSkillsetRequest,
  ) => Promise<SkillsetListItem>;
  deleteSkillset: (id: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);
  const createSkillset = useCallback(
    async (req: CreateSkillsetRequest): Promise<SkillsetListItem> => {
      const res = await fetch('/api/v1/skillsets', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to create skillset: ${res.status}`),
        );
      return res.json();
    },
    [accessToken],
  );
  const updateSkillset = useCallback(
    async (
      id: string,
      req: UpdateSkillsetRequest,
    ): Promise<SkillsetListItem> => {
      const res = await fetch(`/api/v1/skillsets/${id}`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to update skillset: ${res.status}`),
        );
      return res.json();
    },
    [accessToken],
  );
  const deleteSkillset = useCallback(
    async (id: string): Promise<void> => {
      const res = await fetch(`/api/v1/skillsets/${id}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok) throw new Error(`Failed to delete skillset: ${res.status}`);
    },
    [accessToken],
  );
  return { createSkillset, updateSkillset, deleteSkillset };
}

export function useSkillsList(skillsetId: string | null): {
  skills: SkillItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const {
    data: skills,
    loading,
    error,
  } = useAsyncResource<SkillItem[]>(
    resourceKey('skills', skillsetId, auth_required, accessToken, tick),
    !skillsetId || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(`/api/v1/skillsets/${skillsetId}/skills`, {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok) throw new Error(`Failed to load skills: ${res.status}`);
          const data: { skills: SkillItem[] } = await res.json();
          return data.skills ?? [];
        },
    [],
  );

  return { skills, loading, error, refresh };
}

export function useSkillVersionsList(
  skillsetId: string | null,
  skillId: string | null,
): {
  versions: SkillVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const {
    data: versions,
    loading,
    error,
  } = useAsyncResource<SkillVersion[]>(
    resourceKey(
      'skill-versions',
      skillsetId,
      skillId,
      auth_required,
      accessToken,
    ),
    !skillsetId || !skillId || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(
            `/api/v1/skillsets/${skillsetId}/skills/${skillId}/versions`,
            { headers: getApiHeaders(accessToken) },
          );
          if (!res.ok)
            throw new Error(`Failed to load skill versions: ${res.status}`);
          const data: { versions: SkillVersion[] } = await res.json();
          return data.versions ?? [];
        },
    [],
  );

  return { versions, loading, error };
}

export function useSkillMutations(skillsetId: string): {
  createSkill: (req: CreateSkillRequest) => Promise<SkillItem>;
  updateSkill: (skillId: string, req: UpdateSkillRequest) => Promise<SkillItem>;
  deleteSkill: (skillId: string) => Promise<void>;
  renderSkill: (
    skillId: string,
    args: Record<string, unknown>,
  ) => Promise<{ text: string }>;
} {
  const { accessToken } = useContext(AuthContext);
  const createSkill = useCallback(
    async (req: CreateSkillRequest): Promise<SkillItem> => {
      const res = await fetch(`/api/v1/skillsets/${skillsetId}/skills`, {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to create skill: ${res.status}`),
        );
      return res.json();
    },
    [accessToken, skillsetId],
  );
  const updateSkill = useCallback(
    async (skillId: string, req: UpdateSkillRequest): Promise<SkillItem> => {
      const res = await fetch(
        `/api/v1/skillsets/${skillsetId}/skills/${skillId}`,
        {
          method: 'PUT',
          headers: {
            ...getApiHeaders(accessToken),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(req),
        },
      );
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to update skill: ${res.status}`),
        );
      return res.json();
    },
    [accessToken, skillsetId],
  );
  const deleteSkill = useCallback(
    async (skillId: string): Promise<void> => {
      const res = await fetch(
        `/api/v1/skillsets/${skillsetId}/skills/${skillId}`,
        {
          method: 'DELETE',
          headers: getApiHeaders(accessToken),
        },
      );
      if (!res.ok) throw new Error(`Failed to delete skill: ${res.status}`);
    },
    [accessToken, skillsetId],
  );
  const renderSkill = useCallback(
    async (
      skillId: string,
      args: Record<string, unknown>,
    ): Promise<{ text: string }> => {
      const res = await fetch(
        `/api/v1/skillsets/${skillsetId}/skills/${skillId}/render`,
        {
          method: 'POST',
          headers: {
            ...getApiHeaders(accessToken),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ arguments: args }),
        },
      );
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to render skill: ${res.status}`),
        );
      return res.json();
    },
    [accessToken, skillsetId],
  );
  return { createSkill, updateSkill, deleteSkill, renderSkill };
}
