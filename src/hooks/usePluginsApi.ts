import { use, useCallback, useEffect, useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { getApiHeaders, errorMessage } from 'src/hooks/useReportsApi';

export interface PluginDiagnostic {
  severity: 'error' | 'warning';
  code: string;
  message: string;
  path?: string | null;
  skill?: string | null;
}

export interface PluginListItem {
  plugin_id: string;
  name: string;
  package_version?: string | null;
  description: string;
  enabled: boolean;
  current_revision: number;
  package_digest: string;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by?: string | null;
  diagnostics: PluginDiagnostic[];
}

export interface CreatePluginRequest {
  /** The Seizu namespace is derived from this, never supplied separately. */
  name: string;
  version: string;
  description: string;
}

export interface PluginVersion {
  plugin_id: string;
  revision: number;
  manifest: Record<string, unknown>;
  package_digest: string;
  created_at: string;
  created_by: string;
  comment?: string | null;
  diagnostics: PluginDiagnostic[];
}

/**
 * One file in a staged package. A file the editor changed carries its bytes;
 * one it did not carries only the digest the server already stores, so binary
 * assets are never re-uploaded to publish a one-line Markdown edit.
 */
export type StagedFilePayload = {
  path: string;
  media_type?: string;
  executable?: boolean;
} & ({ content_base64: string } | { sha256: string });

export interface PluginToolParam {
  name: string;
  type: string;
  description?: string | null;
  required: boolean;
  default?: unknown;
}

export interface PluginSkillItem {
  plugin_id: string;
  skill_id: string;
  portable_name: string;
  title: string;
  description: string;
  template: string;
  parameters: PluginToolParam[];
  triggers: string[];
  allowed_tools: string[];
  enabled: boolean;
  source_path: string;
  aliases: string[];
  revision: number;
  package_digest: string;
  has_scripts: boolean;
}

export interface PluginFileInfo {
  path: string;
  media_type: string;
  size: number;
  sha256: string;
  executable: boolean;
  etag: string;
}

function base64ToBytes(value: string): Uint8Array {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
}

function encodePluginPath(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

export function usePluginsList() {
  const { accessToken } = use(AuthContext);
  const [plugins, setPlugins] = useState<PluginListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setTick((value) => value + 1);
  }, []);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    fetch('/api/v1/plugins', {
      headers: getApiHeaders(accessToken),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            await errorMessage(response, 'Failed to load plugins'),
          );
        return response.json() as Promise<{ plugins: PluginListItem[] }>;
      })
      .then((body) => !cancelled && setPlugins(body.plugins))
      .catch((reason) => !cancelled && setError(reason as Error))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [accessToken, tick]);
  return { plugins, loading, error, refresh };
}

export function usePluginVersionsList(pluginId: string | null) {
  const { accessToken } = use(AuthContext);
  const [versions, setVersions] = useState<PluginVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    if (!pluginId) return;
    let cancelled = false;
    const controller = new AbortController();
    fetch(`/api/v1/plugins/${encodeURIComponent(pluginId)}/versions`, {
      headers: getApiHeaders(accessToken),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            await errorMessage(response, 'Failed to load plugin versions'),
          );
        return response.json() as Promise<{ versions: PluginVersion[] }>;
      })
      .then((body) => {
        if (cancelled) return;
        setVersions(body.versions);
        setError(null);
      })
      .catch((reason) => !cancelled && setError(reason as Error))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [accessToken, pluginId]);
  return { versions, loading, error };
}

/**
 * A revision's skills and file manifest, loaded together.
 *
 * `revision` is required rather than defaulting to current: the same view backs
 * the version-history dialog, where the whole point is to see a revision that
 * is not the current one.
 */
export function usePluginContents(
  pluginId: string | null,
  revision: number | null,
) {
  const { accessToken } = use(AuthContext);
  const [skills, setSkills] = useState<PluginSkillItem[]>([]);
  const [files, setFiles] = useState<PluginFileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    if (!pluginId || revision === null) return;
    let cancelled = false;
    const controller = new AbortController();
    const base = `/api/v1/plugins/${encodeURIComponent(pluginId)}/versions/${revision}`;
    const load = async (path: string) => {
      const response = await fetch(path, {
        headers: getApiHeaders(accessToken),
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(
          await errorMessage(response, 'Failed to load plugin contents'),
        );
      return response.json();
    };
    Promise.all([load(`${base}/skills`), load(`${base}/files`)])
      .then(([skillBody, fileBody]) => {
        if (cancelled) return;
        setSkills((skillBody as { skills: PluginSkillItem[] }).skills);
        setFiles((fileBody as { files: PluginFileInfo[] }).files);
        setError(null);
      })
      .catch((reason) => !cancelled && setError(reason as Error))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [accessToken, pluginId, revision]);
  return { skills, files, loading, error };
}

export function usePluginMutations() {
  const { accessToken } = use(AuthContext);
  const headers = useCallback(() => getApiHeaders(accessToken), [accessToken]);
  const checked = useCallback(async (response: Response) => {
    if (!response.ok)
      throw new Error(
        await errorMessage(
          response,
          `Plugin request failed: ${response.status}`,
        ),
      );
    return response;
  }, []);

  return {
    create: async (body: CreatePluginRequest): Promise<PluginListItem> =>
      (
        await checked(
          await fetch('/api/v1/plugins', {
            method: 'POST',
            headers: { ...headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          }),
        )
      ).json(),
    install: async (file: File): Promise<PluginListItem> => {
      const body = new FormData();
      body.append('package', file);
      return (
        await checked(
          await fetch('/api/v1/plugins/install', {
            method: 'POST',
            headers: headers(),
            body,
          }),
        )
      ).json();
    },
    setEnabled: async (pluginId: string, enabled: boolean) =>
      checked(
        await fetch(`/api/v1/plugins/${pluginId}`, {
          method: 'PUT',
          headers: { ...headers(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled }),
        }),
      ),
    remove: async (pluginId: string) =>
      checked(
        await fetch(`/api/v1/plugins/${pluginId}`, {
          method: 'DELETE',
          headers: headers(),
        }),
      ),
    get: async (pluginId: string): Promise<PluginListItem> =>
      (
        await checked(
          await fetch(`/api/v1/plugins/${encodeURIComponent(pluginId)}`, {
            headers: headers(),
          }),
        )
      ).json(),
    listFiles: async (
      pluginId: string,
      revision: number,
    ): Promise<PluginFileInfo[]> => {
      const response = await checked(
        await fetch(
          `/api/v1/plugins/${encodeURIComponent(pluginId)}/versions/${revision}/files`,
          { headers: headers() },
        ),
      );
      return ((await response.json()) as { files: PluginFileInfo[] }).files;
    },
    readFile: async (pluginId: string, revision: number, path: string) => {
      const response = await checked(
        await fetch(
          `/api/v1/plugins/${encodeURIComponent(pluginId)}/versions/${revision}/files/${encodePluginPath(path)}`,
          { headers: headers() },
        ),
      );
      const body = (await response.json()) as {
        content_base64: string;
        media_type: string;
        executable: boolean;
      };
      return { ...body, bytes: base64ToBytes(body.content_base64) };
    },
    setSkillEnabled: async (
      pluginId: string,
      skillId: string,
      enabled: boolean,
    ) => {
      const response = await checked(
        await fetch(
          `/api/v1/plugins/${encodeURIComponent(pluginId)}/skills/${encodeURIComponent(skillId)}`,
          {
            method: 'PUT',
            headers: { ...headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
          },
        ),
      );
      return response.json() as Promise<PluginSkillItem>;
    },
    validatePackage: async (pluginId: string, files: StagedFilePayload[]) => {
      const response = await checked(
        await fetch(
          `/api/v1/plugins/${encodeURIComponent(pluginId)}/validate`,
          {
            method: 'POST',
            headers: { ...headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ files }),
          },
        ),
      );
      return response.json() as Promise<{
        valid: boolean;
        diagnostics: PluginDiagnostic[];
      }>;
    },
    publishPackage: async (
      pluginId: string,
      files: StagedFilePayload[],
      baseRevision: number,
      comment?: string,
    ) => {
      const response = await checked(
        await fetch(`/api/v1/plugins/${encodeURIComponent(pluginId)}/publish`, {
          method: 'POST',
          headers: { ...headers(), 'Content-Type': 'application/json' },
          body: JSON.stringify({
            files,
            base_revision: baseRevision,
            comment: comment || null,
          }),
        }),
      );
      return response.json() as Promise<PluginListItem>;
    },
    restore: async (
      pluginId: string,
      revision: number,
      baseRevision: number,
      comment?: string,
    ) => {
      const response = await checked(
        await fetch(`/api/v1/plugins/${encodeURIComponent(pluginId)}/restore`, {
          method: 'POST',
          headers: { ...headers(), 'Content-Type': 'application/json' },
          body: JSON.stringify({
            revision,
            base_revision: baseRevision,
            comment: comment || null,
          }),
        }),
      );
      return response.json() as Promise<PluginListItem>;
    },
  };
}
