import { useState, useEffect, useContext, useCallback } from 'react';
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

export interface ReportListItem {
  report_id: string;
  name: string;
  description: string;
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string;
  access: ReportAccess;
  pinned: boolean;
}

export interface ReportAccess {
  scope: 'private' | 'public';
}

export interface ReportVersion {
  report_id: string;
  name: string;
  version: number;
  config: Report;
  created_at: string;
  created_by: string;
  report_created_by: string;
  report_updated_by: string;
  access: ReportAccess;
  comment: string | null;
  query_capabilities?: Record<string, string>;
}

const REPORT_QUERY_CAPABILITIES_QUERY = '?include_query_capabilities=true';
const REPORTS_LIST_PAGE_SIZE = 500;

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

async function errorMessage(res: Response, fallback: string): Promise<string> {
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
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const handler = () => setTick((t) => t + 1);
    window.addEventListener(REPORTS_UPDATED, handler);
    return () => window.removeEventListener(REPORTS_UPDATED, handler);
  }, []);

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
  const [dashboardReportId, setDashboardReportId] = useState<string | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch('/api/v1/reports/dashboard', { headers: getApiHeaders(accessToken) })
      .then((res) => {
        if (res.status === 404) {
          setDashboardReportId(null);
          setLoading(false);
          return null;
        }
        if (!res.ok) throw new Error(`Failed to load dashboard: ${res.status}`);
        return res.json();
      })
      .then((data: ReportVersion | null) => {
        setDashboardReportId(data?.report_id ?? null);
        setLoading(false);
      })
      .catch(() => {
        setDashboardReportId(null);
        setLoading(false);
      });
  }, [accessToken, auth_required, tick]);

  return { dashboardReportId, loading, refresh };
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
  // Initialize from cache immediately to skip the loading flash on repeat visits
  const [report, setReport] = useState<Report | undefined>(
    dashboardCacheEntry?.report,
  );
  const [queryCapabilities, setQueryCapabilities] = useState<
    Record<string, string> | undefined
  >(dashboardCacheEntry?.queryCapabilities);
  const [loading, setLoading] = useState(!dashboardCacheEntry);
  const [notConfigured, setNotConfigured] = useState(false);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => {
    dashboardCacheEntry = null;
    setTick((t) => t + 1);
  }, []);

  useEffect(() => {
    if (auth_required && !accessToken) return;

    // Serve from cache if populated
    if (dashboardCacheEntry) {
      setReport(dashboardCacheEntry.report);
      setQueryCapabilities(dashboardCacheEntry.queryCapabilities);
      setLoading(false);
      return;
    }

    setLoading(true);
    setQueryCapabilities(undefined);
    setNotConfigured(false);

    let cancelled = false;

    fetch(`/api/v1/reports/dashboard${REPORT_QUERY_CAPABILITIES_QUERY}`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (res.status === 404) {
          if (!cancelled) {
            setNotConfigured(true);
            setLoading(false);
          }
          return null;
        }
        if (!res.ok)
          throw new Error(`Failed to load dashboard report: ${res.status}`);
        return res.json();
      })
      .then((data: ReportVersion | null) => {
        if (cancelled) return;
        if (data) {
          dashboardCacheEntry = {
            report: data.config,
            queryCapabilities: data.query_capabilities,
          };
          setReport(data.config);
          setQueryCapabilities(data.query_capabilities);
        }
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setNotConfigured(true);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [accessToken, auth_required, tick]);

  return { report, queryCapabilities, loading, notConfigured, refresh };
}

export function useAllReports(): {
  reports: Report[];
  loading: boolean;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (auth_required && !accessToken) return;

    setLoading(true);

    fetch('/api/v1/reports', { headers: getApiHeaders(accessToken) })
      .then((res) => {
        if (!res.ok)
          throw new Error(`Failed to load reports list: ${res.status}`);
        return res.json();
      })
      .then((data: { reports: ReportListItem[] }) => {
        const items = data.reports ?? [];
        return Promise.all(
          items.map((item) =>
            fetch(`/api/v1/reports/${item.report_id}`, {
              headers: getApiHeaders(accessToken),
            })
              .then((res) => res.json())
              .then((v: ReportVersion) => v.config),
          ),
        );
      })
      .then((allReports: Report[]) => {
        setReports(allReports);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, [accessToken, auth_required]);

  return { reports, loading };
}

export function useReportsMutations(): {
  createReport: (name: string) => Promise<ReportListItem>;
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
} {
  const { accessToken } = useContext(AuthContext);

  const createReport = useCallback(
    async (name: string): Promise<ReportListItem> => {
      const res = await fetch('/api/v1/reports', {
        method: 'POST',
        headers: {
          ...getApiHeaders(accessToken),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) throw new Error(`Failed to create report: ${res.status}`);
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

  return {
    createReport,
    cloneReport,
    updateReportVisibility,
    saveReportVersion,
    setDashboardReport,
    pinReport,
    deleteReport,
  };
}

export function useReportVersionsList(reportId: string | undefined): {
  versions: ReportVersion[];
  loading: boolean;
  error: Error | null;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [versions, setVersions] = useState<ReportVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!reportId) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    fetch(`/api/v1/reports/${reportId}/versions`, {
      headers: getApiHeaders(accessToken),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load versions: ${res.status}`);
        return res.json();
      })
      .then((data: { versions: ReportVersion[] }) => {
        setVersions(data.versions ?? []);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [reportId, accessToken, auth_required]);

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
  const [reportVersion, setReportVersion] = useState<ReportVersion | undefined>(
    undefined,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!reportId || !versionNum) return;
    if (auth_required && !accessToken) return;

    setLoading(true);
    setReportVersion(undefined);
    setError(null);

    fetch(
      `/api/v1/reports/${reportId}/versions/${versionNum}${REPORT_QUERY_CAPABILITIES_QUERY}`,
      {
        headers: getApiHeaders(accessToken),
      },
    )
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load version: ${res.status}`);
        return res.json();
      })
      .then((data: ReportVersion) => {
        setReportVersion(data);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err);
        setLoading(false);
      });
  }, [reportId, versionNum, accessToken, auth_required]);

  return { reportVersion, loading, error };
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
  // Initialize from cache immediately to skip the loading flash on repeat visits
  const cachedCandidate = reportId
    ? reportCapabilitiesCache.get(reportId)
    : undefined;
  const cached =
    cachedCandidate && hasValidReportRows(cachedCandidate.report)
      ? cachedCandidate
      : undefined;
  const [report, setReport] = useState<Report | undefined>(cached?.report);
  const [name, setName] = useState<string | undefined>(cached?.name);
  const [reportVersion, setReportVersion] = useState<ReportVersion | undefined>(
    cached?.reportVersion,
  );
  const [queryCapabilities, setQueryCapabilities] = useState<
    Record<string, string> | undefined
  >(cached?.queryCapabilities);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => {
    if (reportId) reportCapabilitiesCache.delete(reportId);
    setTick((t) => t + 1);
  }, [reportId]);

  useEffect(() => {
    if (!reportId) return;
    if (auth_required && !accessToken) return;

    // Serve from cache if populated (navigating back to a previously-loaded report)
    const hit = reportCapabilitiesCache.get(reportId);
    if (hit && hasValidReportRows(hit.report)) {
      setReport(hit.report);
      setName(hit.name);
      setReportVersion(hit.reportVersion);
      setQueryCapabilities(hit.queryCapabilities);
      setLoading(false);
      return;
    }
    if (hit) reportCapabilitiesCache.delete(reportId);

    let cancelled = false;

    setLoading(true);
    setQueryCapabilities(undefined);
    setError(null);

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
          queryCapabilities: data.query_capabilities,
        };
        reportCapabilitiesCache.set(reportId, entry);
        setReport(data.config);
        setName(data.name);
        setReportVersion(data);
        setQueryCapabilities(data.query_capabilities);
        setLoading(false);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [reportId, accessToken, auth_required, tick]);

  return {
    report,
    name,
    reportVersion,
    queryCapabilities,
    loading,
    error,
    refresh,
  };
}
