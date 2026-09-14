import { useState, useEffect, useContext, useCallback } from 'react';
import type { components } from 'src/api/openapi.generated';
import { resourceKey, useAsyncResource } from 'src/hooks/useAsyncResource';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { Report } from 'src/config.context';

// Module-level capability caches — survive navigation and edit↔view mode switches.
// Busted on explicit refresh() or token expiry recovery (same call path).
// After saving a new version, call updateCachedReportCapabilities() to keep it consistent.

interface ReportCacheEntry {
  report: Report;
  name: string;
  reportVersion: ReportVersion;
  queryCapabilities: Record<string, string> | undefined;
}

interface DashboardCacheEntry {
  report: Report;
  queryCapabilities: Record<string, string> | undefined;
}

const reportCapabilitiesCache = new Map<string, ReportCacheEntry>();
let dashboardCacheEntry: DashboardCacheEntry | null = null;

function hasValidReportRows(report: Report): boolean {
  return (
    Array.isArray(report.rows) &&
    report.rows.every((row) => Array.isArray(row.panels))
  );
}

export function updateCachedReportCapabilities(
  reportId: string,
  entry: ReportCacheEntry,
): void {
  reportCapabilitiesCache.set(reportId, entry);
}

export function clearCapabilitiesCache(): void {
  reportCapabilitiesCache.clear();
  dashboardCacheEntry = null;
}

export type ReportListItem = components['schemas']['ReportListItem'];
export type ReportAccess = components['schemas']['ReportAccess'];
export type ReportVersion = Omit<
  components['schemas']['ReportVersion'],
  'config'
> & { config: Report };

const REPORT_QUERY_CAPABILITIES_QUERY = '?include_query_capabilities=true';
const REPORTS_LIST_PAGE_SIZE = 500;

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

export async function errorMessage(
  res: Response,
  fallback: string,
): Promise<string> {
  const data = await res.json().catch(() => ({}));
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  const error = (data as { error?: unknown }).error;
  if (typeof error === 'string' && error.trim()) return error;
  const errors = (data as { errors?: unknown }).errors;
  if (
    Array.isArray(errors) &&
    errors.every((error) => typeof error === 'string')
  ) {
    return errors.join(', ');
  }
  return fallback;
}

const REPORTS_UPDATED = 'seizu:reports-updated';

function broadcastReportsUpdated() {
  window.dispatchEvent(new Event(REPORTS_UPDATED));
}

/**
 * Tell every mounted report list to reload.
 *
 * Exported so other modules (creating or deleting a space also creates or
 * deletes a report) can invalidate without re-declaring the event name, which
 * would silently drift the day it changes.
 */
export function notifyReportsUpdated() {
  broadcastReportsUpdated();
}

/**
 * Subscribe to report-list invalidations; the returned counter changes on each.
 *
 * Exported so other modules can react to report changes without re-declaring
 * the event name, which would drift the day it changes.
 */
export function useReportsUpdatedSignal(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const handler = () => setTick((t) => t + 1);
    window.addEventListener(REPORTS_UPDATED, handler);
    return () => window.removeEventListener(REPORTS_UPDATED, handler);
  }, []);
  return tick;
}

/** Also exported so callers can share the helper rather than re-implementing it. */
export { getApiHeaders };

export function useReportsList(): {
  reports: ReportListItem[];
  total: number;
  page: number;
  perPage: number;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [reports, setReports] = useState<ReportListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(REPORTS_LIST_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const tick = useReportsUpdatedSignal();

  const refresh = useCallback(() => broadcastReportsUpdated(), []);

  useEffect(() => {
    let cancelled = false;

    async function loadReportsPage(
      pageNum: number,
      perPageNum: number,
    ): Promise<{
      reports: ReportListItem[];
      total?: number;
      page?: number;
      per_page?: number;
    }> {
      const res = await fetch(
        `/api/v1/reports?page=${pageNum}&per_page=${perPageNum}`,
        {
          headers: getApiHeaders(accessToken),
        },
      );
      if (!res.ok)
        throw new Error(`Failed to load reports list: ${res.status}`);
      return res.json();
    }

    async function loadAllReports(): Promise<void> {
      if (auth_required && !accessToken) return;

      setLoading(true);
      setError(null);

      try {
        const firstPage = await loadReportsPage(1, REPORTS_LIST_PAGE_SIZE);
        if (cancelled) return;

        const pageSize = firstPage.per_page ?? REPORTS_LIST_PAGE_SIZE;
        const totalCount = firstPage.total ?? firstPage.reports?.length ?? 0;
        const firstReports = firstPage.reports ?? [];
        const totalPages = Math.max(Math.ceil(totalCount / pageSize), 1);

        let allReports = firstReports;
        if (totalPages > 1) {
          const remainingPages = await Promise.all(
            Array.from({ length: totalPages - 1 }, (_, index) =>
              loadReportsPage(index + 2, pageSize),
            ),
          );
          if (cancelled) return;
          allReports = [
            ...firstReports,
            ...remainingPages.flatMap((response) => response.reports ?? []),
          ];
        }

        setReports(allReports);
        setTotal(totalCount);
        setPage(firstPage.page ?? 1);
        setPerPage(pageSize);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err as Error);
        setLoading(false);
      }
    }

    void loadAllReports();

    return () => {
      cancelled = true;
    };
  }, [accessToken, auth_required, tick]);

  return { reports, total, page, perPage, loading, error, refresh };
}

export function useDashboardReportId(): {
  dashboardReportId: string | null;
  loading: boolean;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const { data, loading, error } = useAsyncResource<string | null>(
    resourceKey('dashboard-report-id', auth_required, accessToken, tick),
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/reports/dashboard', {
            headers: getApiHeaders(accessToken),
          });
          if (res.status === 404) return null;
          if (!res.ok)
            throw new Error(`Failed to load dashboard: ${res.status}`);
          const version: ReportVersion = await res.json();
          return version.report_id ?? null;
        },
    null,
  );

  return {
    dashboardReportId: error ? null : data,
    loading,
    refresh,
  };
}

export function useDashboardReport(): {
  report: Report | undefined;
  queryCapabilities: Record<string, string> | undefined;
  loading: boolean;
  notConfigured: boolean;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [result, setResult] = useState<{
    key: string;
    entry: DashboardCacheEntry | null;
    notConfigured: boolean;
  } | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => {
    dashboardCacheEntry = null;
    setTick((t) => t + 1);
  }, []);

  const requestKey = resourceKey(
    'dashboard-report',
    auth_required,
    accessToken,
    tick,
  );
  // Read during render, not from an effect: serving the cache from an effect
  // is what put a loading flash on every repeat visit, and the effect then had
  // to set three states to undo it.
  const cached = dashboardCacheEntry;
  const settled = result !== null && result.key === requestKey ? result : null;

  useEffect(() => {
    if (auth_required && !accessToken) return undefined;
    if (dashboardCacheEntry) return undefined;

    let cancelled = false;

    fetch(`/api/v1/reports/dashboard${REPORT_QUERY_CAPABILITIES_QUERY}`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (res.status === 404) {
          if (!cancelled)
            setResult({ key: requestKey, entry: null, notConfigured: true });
          return null;
        }
        if (!res.ok)
          throw new Error(`Failed to load dashboard report: ${res.status}`);
        return res.json();
      })
      .then((data: ReportVersion | null) => {
        if (cancelled || !data) return;
        const entry: DashboardCacheEntry = {
          report: data.config,
          queryCapabilities: data.query_capabilities ?? undefined,
        };
        dashboardCacheEntry = entry;
        setResult({ key: requestKey, entry, notConfigured: false });
      })
      .catch(() => {
        if (!cancelled)
          setResult({ key: requestKey, entry: null, notConfigured: true });
      });

    return () => {
      cancelled = true;
    };
  }, [requestKey, accessToken, auth_required]);

  // As in `useReport`: the dashboard already on screen stays on screen while
  // the next load runs.
  const entry = cached ?? result?.entry ?? null;
  return {
    report: entry?.report,
    queryCapabilities: entry?.queryCapabilities,
    loading: cached === null && settled === null,
    notConfigured: settled?.notConfigured ?? false,
    refresh,
  };
}

export function useAllReports(): {
  reports: Report[];
  loading: boolean;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const { data: reports, loading } = useAsyncResource<Report[]>(
    resourceKey('all-reports', auth_required, accessToken),
    auth_required && !accessToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/reports', {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load reports list: ${res.status}`);
          const data: { reports: ReportListItem[] } = await res.json();
          return Promise.all(
            (data.reports ?? []).map((item) =>
              fetch(`/api/v1/reports/${item.report_id}`, {
                headers: getApiHeaders(accessToken),
              })
                .then((response) => response.json())
                .then((version: ReportVersion) => version.config),
            ),
          );
        },
    [],
  );

  return { reports, loading };
}

export function useReportsMutations(): {
  createReport: (
    name: string,
    spaceId?: string | null,
    subspaceId?: string | null,
  ) => Promise<ReportListItem>;
  cloneReport: (reportId: string, name: string) => Promise<ReportListItem>;
  updateReportVisibility: (
    reportId: string,
    scope: ReportAccess['scope'],
  ) => Promise<ReportListItem>;
  saveReportVersion: (
    reportId: string,
    config: Report,
    comment?: string,
    includeQueryCapabilities?: boolean,
  ) => Promise<ReportVersion>;
  setDashboardReport: (reportId: string) => Promise<void>;
  pinReport: (reportId: string, pinned: boolean) => Promise<void>;
  deleteReport: (reportId: string) => Promise<void>;
  setReportSpace: (
    reportId: string,
    spaceId: string | null,
    subspaceId: string | null,
  ) => Promise<ReportListItem>;
} {
  const { accessToken } = useContext(AuthContext);

  const createReport = useCallback(
    async (
      name: string,
      spaceId: string | null = null,
      subspaceId: string | null = null,
    ): Promise<ReportListItem> => {
      const res = await fetch('/api/v1/reports', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name,
          space_id: spaceId,
          subspace_id: subspaceId,
        }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to create report: ${res.status}`),
        );
      broadcastReportsUpdated();
      return res.json();
    },
    [accessToken],
  );

  const cloneReport = useCallback(
    async (reportId: string, name: string): Promise<ReportListItem> => {
      const res = await fetch(`/api/v1/reports/${reportId}/clone`, {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) throw new Error(`Failed to clone report: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const saveReportVersion = useCallback(
    async (
      reportId: string,
      config: Report,
      comment?: string,
      includeQueryCapabilities: boolean = false,
    ): Promise<ReportVersion> => {
      const res = await fetch(
        `/api/v1/reports/${reportId}/versions?include_query_capabilities=${includeQueryCapabilities}`,
        {
          method: 'POST',
          headers: {
            ...getApiHeaders(accessToken),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ config, comment: comment ?? null }),
        },
      );
      if (!res.ok)
        throw new Error(`Failed to save report version: ${res.status}`);
      return res.json();
    },
    [accessToken],
  );

  const updateReportVisibility = useCallback(
    async (
      reportId: string,
      scope: ReportAccess['scope'],
    ): Promise<ReportListItem> => {
      const res = await fetch(`/api/v1/reports/${reportId}/visibility`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ access: { scope } }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(
            res,
            `Failed to update report visibility: ${res.status}`,
          ),
        );
      return res.json();
    },
    [accessToken],
  );

  const setDashboardReport = useCallback(
    async (reportId: string): Promise<void> => {
      const res = await fetch(`/api/v1/reports/${reportId}/dashboard`, {
        method: 'PUT',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok) throw new Error(`Failed to set dashboard: ${res.status}`);
      // Bust the module-level cache so the next visit to the dashboard
      // re-fetches the newly-selected report instead of serving the old one.
      dashboardCacheEntry = null;
    },
    [accessToken],
  );

  const pinReport = useCallback(
    async (reportId: string, pinned: boolean): Promise<void> => {
      const res = await fetch(`/api/v1/reports/${reportId}/pin`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ pinned }),
      });
      if (!res.ok) throw new Error(`Failed to update pin: ${res.status}`);
    },
    [accessToken],
  );

  const deleteReport = useCallback(
    async (reportId: string): Promise<void> => {
      const res = await fetch(`/api/v1/reports/${reportId}`, {
        method: 'DELETE',
        headers: getApiHeaders(accessToken),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to delete report: ${res.status}`),
        );
    },
    [accessToken],
  );

  const setReportSpace = useCallback(
    async (
      reportId: string,
      spaceId: string | null,
      subspaceId: string | null,
    ): Promise<ReportListItem> => {
      // Replace semantics, matching the API: both fields describe the desired
      // final state, so a move with no sub-space clears it.
      const res = await fetch(`/api/v1/reports/${reportId}/space`, {
        method: 'PUT',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ space_id: spaceId, subspace_id: subspaceId }),
      });
      if (!res.ok)
        throw new Error(
          await errorMessage(res, `Failed to move report: ${res.status}`),
        );
      broadcastReportsUpdated();
      return res.json();
    },
    [accessToken],
  );

  return {
    createReport,
    cloneReport,
    updateReportVisibility,
    saveReportVersion,
    setDashboardReport,
    pinReport,
    deleteReport,
    setReportSpace,
  };
}

export function useReportVersionsList(reportId: string | undefined): {
  versions: ReportVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const {
    data: versions,
    loading,
    error,
  } = useAsyncResource<ReportVersion[]>(
    resourceKey('report-versions', reportId, auth_required, accessToken),
    !reportId || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(`/api/v1/reports/${reportId}/versions`, {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load versions: ${res.status}`);
          const data: { versions: ReportVersion[] } = await res.json();
          return data.versions ?? [];
        },
    [],
  );

  return { versions, loading, error };
}

export function useReportVersion(
  reportId: string | undefined,
  versionNum: string | undefined,
): {
  reportVersion: ReportVersion | undefined;
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const { data, loading, error } = useAsyncResource<ReportVersion | undefined>(
    resourceKey(
      'report-version',
      reportId,
      versionNum,
      auth_required,
      accessToken,
    ),
    !reportId || !versionNum || (auth_required && !accessToken)
      ? null
      : async () => {
          const res = await fetch(
            `/api/v1/reports/${reportId}/versions/${versionNum}${REPORT_QUERY_CAPABILITIES_QUERY}`,
            { headers: getApiHeaders(accessToken) },
          );
          if (!res.ok) throw new Error(`Failed to load version: ${res.status}`);
          return (await res.json()) as ReportVersion;
        },
    undefined,
  );

  // A version already on screen is not an answer about the one being asked
  // for: this view blanks while it loads rather than showing the last one.
  return { reportVersion: loading ? undefined : data, loading, error };
}

export function useReport(reportId: string | undefined): {
  report: Report | undefined;
  name: string | undefined;
  reportVersion: ReportVersion | undefined;
  queryCapabilities: Record<string, string> | undefined;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [result, setResult] = useState<{
    key: string;
    entry: ReportCacheEntry | null;
    error: Error | null;
  } | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => {
    if (reportId) reportCapabilitiesCache.delete(reportId);
    setTick((t) => t + 1);
  }, [reportId]);

  const requestKey = resourceKey(
    'report',
    reportId,
    auth_required,
    accessToken,
    tick,
  );
  // Read during render, not copied out of the cache by an effect: serving a hit
  // from an effect costs a loading flash on every repeat visit, and then five
  // state writes to take it back.
  const cachedCandidate = reportId
    ? reportCapabilitiesCache.get(reportId)
    : undefined;
  const cached =
    cachedCandidate && hasValidReportRows(cachedCandidate.report)
      ? cachedCandidate
      : undefined;
  const settled = result !== null && result.key === requestKey ? result : null;

  useEffect(() => {
    if (!reportId) return undefined;
    if (auth_required && !accessToken) return undefined;

    const hit = reportCapabilitiesCache.get(reportId);
    if (hit && hasValidReportRows(hit.report)) return undefined;
    if (hit) reportCapabilitiesCache.delete(reportId);

    let cancelled = false;

    fetch(`/api/v1/reports/${reportId}${REPORT_QUERY_CAPABILITIES_QUERY}`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load report: ${res.status}`);
        return res.json();
      })
      .then((data: ReportVersion) => {
        if (cancelled) return;
        const entry: ReportCacheEntry = {
          report: data.config,
          name: data.name,
          reportVersion: data,
          queryCapabilities: data.query_capabilities ?? undefined,
        };
        reportCapabilitiesCache.set(reportId, entry);
        setResult({ key: requestKey, entry, error: null });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setResult({ key: requestKey, entry: null, error: err });
      });

    return () => {
      cancelled = true;
    };
  }, [requestKey, reportId, accessToken, auth_required]);

  // The last report loaded stays readable while the next one is fetched, which
  // is what kept a refresh from blanking the page it is refreshing.
  const entry = cached ?? result?.entry ?? null;

  return {
    report: entry?.report,
    name: entry?.name,
    reportVersion: entry?.reportVersion,
    queryCapabilities: entry?.queryCapabilities,
    loading: cached === undefined && settled === null,
    error: settled?.error ?? null,
    refresh,
  };
}
