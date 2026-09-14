import { useState, useContext, useCallback } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { resourceKey, useAsyncResource } from 'src/hooks/useAsyncResource';

export interface RoleItem {
  role_id: string;
  name: string;
  description: string;
  permissions: string[];
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
}

export interface RoleVersion {
  role_id: string;
  name: string;
  description: string;
  permissions: string[];
  version: number;
  created_at: string;
  created_by: string;
  comment: string | null;
}

export interface CreateRoleRequest {
  name: string;
  description: string;
  permissions: string[];
  comment?: string | null;
}

export interface UpdateRoleRequest {
  name: string;
  description: string;
  permissions: string[];
  comment?: string | null;
}

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

export function isBuiltinRole(roleId: string): boolean {
  return roleId.startsWith('builtin:');
}

export function useBuiltinRolesList(enabled = true): {
  roles: RoleItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const {
    data: roles,
    loading,
    error,
  } = useAsyncResource<RoleItem[]>(
    enabled
      ? resourceKey('roles/builtin', auth_required, accessToken, tick)
      : null,
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/roles/builtin', {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load built-in roles: ${res.status}`);
          const data: { roles: RoleItem[] } = await res.json();
          return data.roles ?? [];
        },
    [],
  );

  return { roles, loading, error, refresh };
}

export function useRolesList(enabled = true): {
  roles: RoleItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const {
    data: roles,
    loading,
    error,
  } = useAsyncResource<RoleItem[]>(
    enabled ? resourceKey('roles', auth_required, accessToken, tick) : null,
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/roles', {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok) throw new Error(`Failed to load roles: ${res.status}`);
          const data: { roles: RoleItem[] } = await res.json();
          return data.roles ?? [];
        },
    [],
  );

  return { roles, loading, error, refresh };
}

export function useRoleVersionsList(
  roleId: string | null,
  enabled = true,
): {
  versions: RoleVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const {
    data: versions,
    loading,
    error,
  } = useAsyncResource<RoleVersion[]>(
    enabled && roleId
      ? resourceKey('role-versions', roleId, auth_required, accessToken)
      : null,
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch(`/api/v1/roles/${roleId}/versions`, {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load role versions: ${res.status}`);
          const data: { versions: RoleVersion[] } = await res.json();
          return data.versions ?? [];
        },
    [],
  );

  return { versions, loading, error };
}

export function useRoleMutations(): {
  createRole: (req: CreateRoleRequest) => Promise<RoleItem>;
  updateRole: (id: string, req: UpdateRoleRequest) => Promise<RoleItem>;
  deleteRole: (id: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);

  const createRole = useCallback(
    async (req: CreateRoleRequest): Promise<RoleItem> => {
      const res = await fetch('/api/v1/roles', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) throw new Error(`Failed to create role: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const updateRole = useCallback(
    async (id: string, req: UpdateRoleRequest): Promise<RoleItem> => {
      const res = await fetch(`/api/v1/roles/${id}`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) throw new Error(`Failed to update role: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const deleteRole = useCallback(
    async (id: string): Promise<void> => {
      const res = await fetch(`/api/v1/roles/${id}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok) throw new Error(`Failed to delete role: ${res.status}`);
    },
    [accessToken],
  );

  return { createRole, updateRole, deleteRole };
}
