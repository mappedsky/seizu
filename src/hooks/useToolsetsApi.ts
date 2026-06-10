import { useState, useEffect, useContext, useCallback } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';

export interface ToolParamDef {
  name: string;
  type: 'string' | 'integer' | 'float' | 'boolean';
  description: string;
  required: boolean;
  default: unknown;
}

export interface ToolsetListItem {
  toolset_id: string;
  name: string;
  description: string;
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
}

export interface ToolsetVersion {
  toolset_id: string;
  name: string;
  description: string;
  enabled: boolean;
  version: number;
  created_at: string;
  created_by: string;
  comment: string | null;
}

export interface ToolItem {
  tool_id: string;
  toolset_id: string;
  name: string;
  description: string;
  cypher: string;
  parameters: ToolParamDef[];
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
  effective_enabled?: boolean | null;
  disabled_reason?: string | null;
}

export interface ToolVersion {
  tool_id: string;
  toolset_id: string;
  name: string;
  description: string;
  cypher: string;
  parameters: ToolParamDef[];
  enabled: boolean;
  version: number;
  created_at: string;
  created_by: string;
  comment: string | null;
}

export interface CreateToolsetRequest {
  toolset_id: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface UpdateToolsetRequest {
  name: string;
  description: string;
  enabled: boolean;
  comment?: string | null;
}

export interface CreateToolRequest {
  tool_id: string;
  name: string;
  description: string;
  cypher: string;
  parameters: ToolParamDef[];
  enabled: boolean;
}

export interface UpdateToolRequest {
  name: string;
  description: string;
  cypher: string;
  parameters: ToolParamDef[];
  enabled: boolean;
  comment?: string | null;
}

export interface ToolCatalogItem {
  mcp_name: string;
  toolset_id: string;
  tool_id: string;
  toolset_name: string;
  name: string;
  enabled: boolean;
}

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

/**
 * The fully-qualified MCP slug for a tool — `toolset__tool` for user tools, or
 * the decoded `group__action` for built-ins. This is the exact name the tool is
 * callable by (in chat, the MCP server, and the CLI).
 */
export function mcpNameForTool(tool: {
  tool_id: string;
  toolset_id: string;
}): string {
  if (tool.tool_id.startsWith('__builtin_') && tool.tool_id.endsWith('__')) {
    return tool.tool_id.slice('__builtin_'.length, -2);
  }
  return `${tool.toolset_id}__${tool.tool_id}`;
}

// ---------------------------------------------------------------------------
// Toolset hooks
// ---------------------------------------------------------------------------

export function useToolsetsList(): {
  toolsets: ToolsetListItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [toolsets, setToolsets] = useState<ToolsetListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch('/api/v1/toolsets', { headers: getApiHeaders(accessToken) })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load toolsets: ${res.status}`);
        return res.json();
      })
      .then((data: { toolsets: ToolsetListItem[] }) => {
        setToolsets(data.toolsets ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [accessToken, auth_required, tick]);

  return { toolsets, loading, error, refresh };
}

export function useToolsetVersionsList(toolsetId: string | null): {
  versions: ToolsetVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [versions, setVersions] = useState<ToolsetVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!toolsetId) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch(`/api/v1/toolsets/${toolsetId}/versions`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok)
          throw new Error(`Failed to load toolset versions: ${res.status}`);
        return res.json();
      })
      .then((data: { versions: ToolsetVersion[] }) => {
        setVersions(data.versions ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [toolsetId, accessToken, auth_required]);

  return { versions, loading, error };
}

export function useToolsetMutations(): {
  createToolset: (req: CreateToolsetRequest) => Promise<ToolsetListItem>;
  updateToolset: (
    id: string,
    req: UpdateToolsetRequest,
  ) => Promise<ToolsetListItem>;
  deleteToolset: (id: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);

  const createToolset = useCallback(
    async (req: CreateToolsetRequest): Promise<ToolsetListItem> => {
      const res = await fetch('/api/v1/toolsets', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) throw new Error(`Failed to create toolset: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const updateToolset = useCallback(
    async (id: string, req: UpdateToolsetRequest): Promise<ToolsetListItem> => {
      const res = await fetch(`/api/v1/toolsets/${id}`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) throw new Error(`Failed to update toolset: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const deleteToolset = useCallback(
    async (id: string): Promise<void> => {
      const res = await fetch(`/api/v1/toolsets/${id}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok) throw new Error(`Failed to delete toolset: ${res.status}`);
    },
    [accessToken],
  );

  return { createToolset, updateToolset, deleteToolset };
}

// ---------------------------------------------------------------------------
// Tool hooks
// ---------------------------------------------------------------------------

export function useToolsList(toolsetId: string | null): {
  tools: ToolItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!toolsetId) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch(`/api/v1/toolsets/${toolsetId}/tools`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load tools: ${res.status}`);
        return res.json();
      })
      .then((data: { tools: ToolItem[] }) => {
        setTools(data.tools ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [toolsetId, accessToken, auth_required, tick]);

  return { tools, loading, error, refresh };
}

export function useToolCatalog(): {
  tools: ToolCatalogItem[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tools, setTools] = useState<ToolCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (auth_required && !accessToken) return;
    let cancelled = false;

    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const toolsetRes = await fetch('/api/v1/toolsets', {
          headers: getApiHeaders(accessToken),
        });
        if (!toolsetRes.ok)
          throw new Error(`Failed to load toolsets: ${toolsetRes.status}`);
        const toolsetData = (await toolsetRes.json()) as {
          toolsets: ToolsetListItem[];
        };
        const toolsets = toolsetData.toolsets ?? [];
        const nested = await Promise.all(
          toolsets.map(async (toolset) => {
            const toolsRes = await fetch(
              `/api/v1/toolsets/${toolset.toolset_id}/tools`,
              {
                headers: getApiHeaders(accessToken),
              },
            );
            if (!toolsRes.ok)
              throw new Error(`Failed to load tools: ${toolsRes.status}`);
            const toolsData = (await toolsRes.json()) as { tools: ToolItem[] };
            return (toolsData.tools ?? []).map((tool) => ({
              mcp_name: mcpNameForTool(tool),
              toolset_id: tool.toolset_id,
              tool_id: tool.tool_id,
              toolset_name: toolset.name,
              name: tool.name,
              enabled: tool.effective_enabled ?? tool.enabled,
            }));
          }),
        );
        if (!cancelled) {
          setTools(
            nested.flat().sort((a, b) => a.mcp_name.localeCompare(b.mcp_name)),
          );
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err as Error);
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [accessToken, auth_required]);

  return { tools, loading, error };
}

export function useToolVersionsList(
  toolsetId: string | null,
  toolId: string | null,
): {
  versions: ToolVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [versions, setVersions] = useState<ToolVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!toolsetId || !toolId) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch(`/api/v1/toolsets/${toolsetId}/tools/${toolId}/versions`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok)
          throw new Error(`Failed to load tool versions: ${res.status}`);
        return res.json();
      })
      .then((data: { versions: ToolVersion[] }) => {
        setVersions(data.versions ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [toolsetId, toolId, accessToken, auth_required]);

  return { versions, loading, error };
}

export function useToolMutations(toolsetId: string): {
  createTool: (req: CreateToolRequest) => Promise<ToolItem>;
  updateTool: (toolId: string, req: UpdateToolRequest) => Promise<ToolItem>;
  deleteTool: (toolId: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);

  const createTool = useCallback(
    async (req: CreateToolRequest): Promise<ToolItem> => {
      const res = await fetch(`/api/v1/toolsets/${toolsetId}/tools`, {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg =
          (data as { errors?: string[] }).errors?.join(', ') ??
          `Failed to create tool: ${res.status}`;
        throw new Error(msg);
      }
      return res.json();
    },
    [accessToken, toolsetId],
  );

  const updateTool = useCallback(
    async (toolId: string, req: UpdateToolRequest): Promise<ToolItem> => {
      const res = await fetch(`/api/v1/toolsets/${toolsetId}/tools/${toolId}`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg =
          (data as { errors?: string[] }).errors?.join(', ') ??
          `Failed to update tool: ${res.status}`;
        throw new Error(msg);
      }
      return res.json();
    },
    [accessToken, toolsetId],
  );

  const deleteTool = useCallback(
    async (toolId: string): Promise<void> => {
      const res = await fetch(`/api/v1/toolsets/${toolsetId}/tools/${toolId}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok) throw new Error(`Failed to delete tool: ${res.status}`);
    },
    [accessToken, toolsetId],
  );

  return { createTool, updateTool, deleteTool };
}

export interface CallToolResponse {
  results: unknown[];
}

export function useToolCall(
  toolsetId: string,
  toolId: string,
): {
  callTool: (args: Record<string, unknown>) => Promise<CallToolResponse>;
} {
  const { accessToken } = useContext(AuthContext);

  const callTool = useCallback(
    async (args: Record<string, unknown>): Promise<CallToolResponse> => {
      const res = await fetch(
        `/api/v1/toolsets/${toolsetId}/tools/${toolId}/call`,
        {
          method: 'POST',
          headers: {
            ...getApiHeaders(accessToken),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ arguments: args }),
        },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg =
          (data as { detail?: string }).detail ??
          `Failed to call tool: ${res.status}`;
        throw new Error(msg);
      }
      return res.json();
    },
    [accessToken, toolsetId, toolId],
  );

  return { callTool };
}
