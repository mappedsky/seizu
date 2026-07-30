import { useCallback, useContext, useEffect, useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import {
  errorMessage,
  getApiHeaders,
  notifyReportsUpdated,
  useReportsUpdatedSignal,
  ReportListItem,
} from 'src/hooks/useReportsApi';

export interface SpaceListItem {
  space_id: string;
  name: string;
  description: string;
  /**
   * Populated only by the tree endpoint, which has the caller's visible report
   * list to resolve it against; the list and get endpoints always return null.
   */
  overview_report_id: string | null;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
}

export interface SubspaceItem {
  subspace_id: string;
  space_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string | null;
}

export interface SpaceTree {
  space: SpaceListItem;
  subspaces: SubspaceItem[];
  /**
   * Reports filed in the space. Members are public (a draft cannot be filed),
   * so this is not narrowed per caller in practice. The API blanks out dangling
   * references before responding — a `subspace_id` whose sub-space is gone, and
   * an `overview_report_id` that is no longer a member — so neither reaches
   * here.
   */
  reports: ReportListItem[];
}

const SPACES_UPDATED = 'seizu:spaces-updated';

function broadcastSpacesUpdated() {
  window.dispatchEvent(new Event(SPACES_UPDATED));
}

function useSpacesTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const handler = () => setTick((t) => t + 1);
    window.addEventListener(SPACES_UPDATED, handler);
    return () => window.removeEventListener(SPACES_UPDATED, handler);
  }, []);
  return tick;
}

export function useSpacesList(): {
  spaces: SpaceListItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [spaces, setSpaces] = useState<SpaceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const tick = useSpacesTick();

  const refresh = useCallback(() => broadcastSpacesUpdated(), []);

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      if (auth_required && !accessToken) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch('/api/v1/spaces', {
          headers: getApiHeaders(accessToken),
        });
        if (!res.ok)
          throw new Error(
            await errorMessage(res, `Failed to load spaces: ${res.status}`),
          );
        const data = await res.json();
        if (cancelled) return;
        setSpaces(data.spaces ?? []);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err as Error);
        setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [accessToken, auth_required, tick]);

  return { spaces, loading, error, refresh };
}

export function useSpaceTree(spaceId: string | null): {
  tree: SpaceTree | null;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tree, setTree] = useState<SpaceTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const tick = useSpacesTick();
  // The tree embeds report names and membership, so it goes stale on report
  // changes too — renaming a report while viewing it inside a space has to
  // update the sidebar entry, not just the pane.
  const reportsTick = useReportsUpdatedSignal();
  const [localTick, setLocalTick] = useState(0);

  const refresh = useCallback(() => setLocalTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      if (!spaceId) {
        setTree(null);
        setLoading(false);
        return;
      }
      if (auth_required && !accessToken) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/api/v1/spaces/${spaceId}/tree`, {
          headers: getApiHeaders(accessToken),
        });
        if (!res.ok)
          throw new Error(
            await errorMessage(res, `Failed to load space: ${res.status}`),
          );
        const data = await res.json();
        if (cancelled) return;
        setTree(data);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err as Error);
        setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [accessToken, auth_required, spaceId, tick, reportsTick, localTick]);

  return { tree, loading, error, refresh };
}

/**
 * The sub-spaces of one space, without pulling its whole report tree.
 *
 * Used by the move dialog, where only the grouping options matter.
 */
export function useSubspacesList(spaceId: string | null): {
  subspaces: SubspaceItem[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [subspaces, setSubspaces] = useState<SubspaceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const tick = useSpacesTick();

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      if (!spaceId) {
        setSubspaces([]);
        setLoading(false);
        return;
      }
      if (auth_required && !accessToken) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/api/v1/spaces/${spaceId}/subspaces`, {
          headers: getApiHeaders(accessToken),
        });
        if (!res.ok)
          throw new Error(
            await errorMessage(res, `Failed to load sub-spaces: ${res.status}`),
          );
        const data = await res.json();
        if (cancelled) return;
        setSubspaces(data.subspaces ?? []);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err as Error);
        setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [accessToken, auth_required, spaceId, tick]);

  return { subspaces, loading, error };
}

export function useSpaceMutations(): {
  createSpace: (name: string, description: string) => Promise<SpaceListItem>;
  setSpaceOverview: (
    spaceId: string,
    reportId: string | null,
  ) => Promise<SpaceListItem>;
  updateSpace: (
    spaceId: string,
    name: string,
    description: string,
  ) => Promise<SpaceListItem>;
  deleteSpace: (spaceId: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);

  const createSpace = useCallback(
    async (name: string, description: string): Promise<SpaceListItem> => {
      const res = await fetch('/api/v1/spaces', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, description }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to create space: ${res.status}`),
        );
      broadcastSpacesUpdated();
      // Creating a space creates its overview report, so report lists (and the
      // pinned-reports sidebar) are stale too.
      notifyReportsUpdated();
      return res.json();
    },
    [accessToken],
  );

  const updateSpace = useCallback(
    async (
      spaceId: string,
      name: string,
      description: string,
    ): Promise<SpaceListItem> => {
      const res = await fetch(`/api/v1/spaces/${spaceId}`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, description }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to update space: ${res.status}`),
        );
      broadcastSpacesUpdated();
      return res.json();
    },
    [accessToken],
  );

  const deleteSpace = useCallback(
    async (spaceId: string): Promise<void> => {
      const res = await fetch(`/api/v1/spaces/${spaceId}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to delete space: ${res.status}`),
        );
      broadcastSpacesUpdated();
      // Deleting a space deletes its overview report.
      notifyReportsUpdated();
    },
    [accessToken],
  );

  const setSpaceOverview = useCallback(
    async (
      spaceId: string,
      reportId: string | null,
    ): Promise<SpaceListItem> => {
      const res = await fetch(`/api/v1/spaces/${spaceId}/overview`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ report_id: reportId }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to set the overview: ${res.status}`),
        );
      broadcastSpacesUpdated();
      return res.json();
    },
    [accessToken],
  );

  return { createSpace, updateSpace, deleteSpace, setSpaceOverview };
}

/** Sub-space mutations. The parent space is baked in, mirroring useToolMutations. */
export function useSubspaceMutations(spaceId: string): {
  createSubspace: (name: string) => Promise<SubspaceItem>;
  updateSubspace: (subspaceId: string, name: string) => Promise<SubspaceItem>;
  deleteSubspace: (subspaceId: string) => Promise<void>;
} {
  const { accessToken } = useContext(AuthContext);

  const createSubspace = useCallback(
    async (name: string): Promise<SubspaceItem> => {
      const res = await fetch(`/api/v1/spaces/${spaceId}/subspaces`, {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to create sub-space: ${res.status}`),
        );
      broadcastSpacesUpdated();
      return res.json();
    },
    [accessToken, spaceId],
  );

  const updateSubspace = useCallback(
    async (subspaceId: string, name: string): Promise<SubspaceItem> => {
      const res = await fetch(
        `/api/v1/spaces/${spaceId}/subspaces/${subspaceId}`,
        {
          method: 'PUT',
          headers: {
            ...getApiHeaders(accessToken),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ name }),
        },
      );
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to rename sub-space: ${res.status}`),
        );
      broadcastSpacesUpdated();
      return res.json();
    },
    [accessToken, spaceId],
  );

  const deleteSubspace = useCallback(
    async (subspaceId: string): Promise<void> => {
      const res = await fetch(
        `/api/v1/spaces/${spaceId}/subspaces/${subspaceId}`,
        {
          method: 'DELETE',
          headers: getApiHeaders(accessToken),
        },
      );
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to delete sub-space: ${res.status}`),
        );
      broadcastSpacesUpdated();
      // Member reports fall back to ungrouped, so their grouping changed.
      notifyReportsUpdated();
    },
    [accessToken, spaceId],
  );

  return { createSubspace, updateSubspace, deleteSubspace };
}
