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
  plugin_id: string;
  name: string;
  version: string;
  description: string;
}

export interface PluginFileInfo {
  path: string;
  media_type: string;
  size: number;
  sha256: string;
  executable: boolean;
  etag: string;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  bytes.forEach((value) => (binary += String.fromCharCode(value)));
  return btoa(binary);
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
    createDraft: async (pluginId: string) =>
      checked(
        await fetch(`/api/v1/plugins/${pluginId}/draft`, {
          method: 'POST',
          headers: headers(),
        }),
      ),
    discardDraft: async (pluginId: string) =>
      checked(
        await fetch(`/api/v1/plugins/${pluginId}/draft`, {
          method: 'DELETE',
          headers: headers(),
        }),
      ),
    listDraftFiles: async (pluginId: string): Promise<PluginFileInfo[]> => {
      const response = await checked(
        await fetch(`/api/v1/plugins/${pluginId}/draft/files`, {
          headers: headers(),
        }),
      );
      return ((await response.json()) as { files: PluginFileInfo[] }).files;
    },
    readDraftFile: async (pluginId: string, path: string) => {
      const response = await checked(
        await fetch(
          `/api/v1/plugins/${pluginId}/draft/files/${encodePluginPath(path)}`,
          { headers: headers() },
        ),
      );
      const body = (await response.json()) as {
        content_base64: string;
        media_type: string;
        executable: boolean;
        etag: string;
      };
      return { ...body, bytes: base64ToBytes(body.content_base64) };
    },
    writeDraftFile: async (
      pluginId: string,
      path: string,
      bytes: Uint8Array,
      mediaType: string,
      etag?: string,
      executable = false,
    ) => {
      const requestHeaders: Record<string, string> = {
        ...headers(),
        'Content-Type': 'application/json',
      };
      if (etag) requestHeaders['If-Match'] = etag;
      const response = await checked(
        await fetch(
          `/api/v1/plugins/${pluginId}/draft/files/${encodePluginPath(path)}`,
          {
            method: 'PUT',
            headers: requestHeaders,
            body: JSON.stringify({
              content_base64: bytesToBase64(bytes),
              media_type: mediaType,
              executable,
            }),
          },
        ),
      );
      return response.json() as Promise<PluginFileInfo>;
    },
    deleteDraftFile: async (pluginId: string, path: string, etag?: string) => {
      const requestHeaders: Record<string, string> = headers();
      if (etag) requestHeaders['If-Match'] = etag;
      return checked(
        await fetch(
          `/api/v1/plugins/${pluginId}/draft/files/${encodePluginPath(path)}`,
          {
            method: 'DELETE',
            headers: requestHeaders,
          },
        ),
      );
    },
    validateDraft: async (pluginId: string) => {
      const response = await checked(
        await fetch(`/api/v1/plugins/${pluginId}/draft/validate`, {
          method: 'POST',
          headers: headers(),
        }),
      );
      return response.json() as Promise<{
        valid: boolean;
        diagnostics: PluginDiagnostic[];
      }>;
    },
    publishDraft: async (pluginId: string, comment?: string) => {
      const response = await checked(
        await fetch(`/api/v1/plugins/${pluginId}/draft/publish`, {
          method: 'POST',
          headers: { ...headers(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ comment: comment || null }),
        }),
      );
      return response.json() as Promise<PluginListItem>;
    },
  };
}
