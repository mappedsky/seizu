import { act, renderHook, waitFor } from '@testing-library/react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import type { Report } from 'src/config.context';
import {
  useReportVersionsList,
  useReportVersion,
  useReportsList,
  useReportsMutations,
  useReport,
  useDashboardReport,
  clearCapabilitiesCache,
  updateCachedReportCapabilities,
} from 'src/hooks/useReportsApi';

const AUTH_CONFIG_NO_OIDC = {
  auth_required: false,
  oidc: null,
  loaded: true,
};

function makeWrapper(authRequired: boolean, accessToken: string | null) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <AuthConfigContext.Provider
        value={{ ...AUTH_CONFIG_NO_OIDC, auth_required: authRequired }}
      >
        <AuthContext.Provider value={{ accessToken, isLoading: false }}>
          {children}
        </AuthContext.Provider>
      </AuthConfigContext.Provider>
    );
  };
}

const VERSIONS = [
  {
    report_id: 'r1',
    name: 'My Report',
    version: 1,
    config: { rows: [] },
    created_at: '2024-01-01T00:00:00Z',
    created_by: 'alice@example.com',
    comment: 'Initial version',
  },
  {
    report_id: 'r1',
    name: 'My Report',
    version: 2,
    config: { rows: [] },
    created_at: '2024-01-02T00:00:00Z',
    created_by: 'bob@example.com',
    comment: null,
  },
];

// ---------------------------------------------------------------------------
// useReportVersionsList
// ---------------------------------------------------------------------------

describe('useReportVersionsList', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('fetches versions for a report', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ versions: VERSIONS }),
    });

    const { result } = renderHook(() => useReportVersionsList('r1'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.versions).toEqual(VERSIONS);
    expect(result.current.error).toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/versions',
      expect.any(Object),
    );
  });

  it('does not fetch when auth_required and accessToken is null', () => {
    renderHook(() => useReportVersionsList('r1'), {
      wrapper: makeWrapper(true, null),
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('does not fetch when reportId is undefined', () => {
    renderHook(() => useReportVersionsList(undefined), {
      wrapper: makeWrapper(false, null),
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('includes Authorization header when accessToken is set', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ versions: VERSIONS }),
    });

    renderHook(() => useReportVersionsList('r1'), {
      wrapper: makeWrapper(true, 'mytoken'),
    });

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/versions',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer mytoken' }),
      }),
    );
  });

  it('sets error when fetch returns non-ok status', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 });

    const { result } = renderHook(() => useReportVersionsList('r1'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.versions).toEqual([]);
  });

  it('sets error when fetch rejects', async () => {
    mockFetch.mockRejectedValue(new Error('network error'));

    const { result } = renderHook(() => useReportVersionsList('r1'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// useReportVersion
// ---------------------------------------------------------------------------

describe('useReportVersion', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('fetches a specific version', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(VERSIONS[0]),
    });

    const { result } = renderHook(() => useReportVersion('r1', '1'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.reportVersion).toEqual(VERSIONS[0]);
    expect(result.current.error).toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/versions/1?include_query_capabilities=true',
      expect.any(Object),
    );
  });

  it('does not fetch when auth_required and accessToken is null', () => {
    renderHook(() => useReportVersion('r1', '1'), {
      wrapper: makeWrapper(true, null),
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('does not fetch when reportId is undefined', () => {
    renderHook(() => useReportVersion(undefined, '1'), {
      wrapper: makeWrapper(false, null),
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('does not fetch when versionNum is undefined', () => {
    renderHook(() => useReportVersion('r1', undefined), {
      wrapper: makeWrapper(false, null),
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('includes Authorization header when accessToken is set', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(VERSIONS[1]),
    });

    renderHook(() => useReportVersion('r1', '2'), {
      wrapper: makeWrapper(true, 'mytoken'),
    });

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/versions/2?include_query_capabilities=true',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer mytoken' }),
      }),
    );
  });

  it('sets error when fetch returns non-ok status', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 404 });

    const { result } = renderHook(() => useReportVersion('r1', '99'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.reportVersion).toBeUndefined();
  });

  it('resets reportVersion when reportId changes', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(VERSIONS[0]),
    });

    const { result, rerender } = renderHook(
      ({ id, ver }: { id: string; ver: string }) => useReportVersion(id, ver),
      {
        initialProps: { id: 'r1', ver: '1' },
        wrapper: makeWrapper(false, null),
      },
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.reportVersion).toEqual(VERSIONS[0]);

    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(VERSIONS[1]),
    });

    rerender({ id: 'r2', ver: '1' });

    // Wait for new data to load after the reportId change
    await waitFor(() =>
      expect(result.current.reportVersion).toEqual(VERSIONS[1]),
    );
    expect(result.current.loading).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// useReportsList
// ---------------------------------------------------------------------------

const REPORTS = [
  {
    report_id: 'r1',
    name: 'Alpha',
    description: '',
    current_version: 2,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
    pinned: false,
  },
  {
    report_id: 'r2',
    name: 'Beta',
    description: '',
    current_version: 1,
    created_at: '2024-01-03T00:00:00Z',
    updated_at: '2024-01-03T00:00:00Z',
    pinned: true,
  },
];

describe('useReportsList', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('fetches and returns reports list', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({ reports: REPORTS, total: 2, page: 1, per_page: 500 }),
    });

    const { result } = renderHook(() => useReportsList(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.reports).toEqual(REPORTS);
    expect(result.current.total).toBe(2);
    expect(result.current.page).toBe(1);
    expect(result.current.perPage).toBe(500);
    expect(result.current.error).toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports?page=1&per_page=500',
      expect.any(Object),
    );
  });

  it('fetches additional report pages when the backend reports more data', async () => {
    const page1Reports = Array.from({ length: 500 }, (_, index) => ({
      report_id: `r${index + 1}`,
      name: `Report ${index + 1}`,
      description: '',
      current_version: 1,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-02T00:00:00Z',
      created_by: 'alice@example.com',
      updated_by: 'alice@example.com',
      access: { scope: 'public' as const },
      pinned: false,
    }));
    const page2Reports = [
      {
        report_id: 'r501',
        name: 'Report 501',
        description: '',
        current_version: 1,
        created_at: '2024-01-03T00:00:00Z',
        updated_at: '2024-01-03T00:00:00Z',
        created_by: 'bob@example.com',
        updated_by: 'bob@example.com',
        access: { scope: 'private' as const },
        pinned: true,
      },
    ];
    const page1 = {
      reports: page1Reports,
      total: 501,
      page: 1,
      per_page: 500,
    };
    const page2 = {
      reports: page2Reports,
      total: 501,
      page: 2,
      per_page: 500,
    };

    mockFetch
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page1) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page2) });

    const { result } = renderHook(() => useReportsList(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.reports).toEqual([
      ...page1.reports,
      ...page2.reports,
    ]);
    expect(result.current.total).toBe(501);
    expect(result.current.page).toBe(1);
    expect(result.current.perPage).toBe(500);
    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/reports?page=1&per_page=500',
      expect.any(Object),
    );
    expect(mockFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/reports?page=2&per_page=500',
      expect.any(Object),
    );
  });

  it('includes pinned field in returned items', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ reports: REPORTS }),
    });

    const { result } = renderHook(() => useReportsList(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.reports[1].pinned).toBe(true);
    expect(result.current.reports[0].pinned).toBe(false);
  });

  it('does not fetch when auth_required and no token', () => {
    renderHook(() => useReportsList(), {
      wrapper: makeWrapper(true, null),
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('refresh dispatches the seizu:reports-updated event', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ reports: REPORTS }),
    });

    const { result } = renderHook(() => useReportsList(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));

    const handler = jest.fn();
    window.addEventListener('seizu:reports-updated', handler);
    result.current.refresh();
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener('seizu:reports-updated', handler);
  });

  it('sets error state on non-ok response', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 });

    const { result } = renderHook(() => useReportsList(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.reports).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// useReportsMutations – saveReportVersion
// ---------------------------------------------------------------------------

describe('useReportsMutations (saveReportVersion)', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('sends the report config name as the single write-side report name', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          report_id: 'r1',
          name: 'Renamed Report',
          version: 2,
          config: { name: 'Renamed Report', rows: [] },
          created_at: '2024-01-02T00:00:00Z',
          created_by: 'alice@example.com',
          comment: 'rename',
        }),
    });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    const config: Report = { name: 'Renamed Report', rows: [] };
    await act(async () => {
      await result.current.saveReportVersion('r1', config, 'rename', true);
    });

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/versions?include_query_capabilities=true',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          config,
          comment: 'rename',
        }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// useReportsMutations – updateReportVisibility
// ---------------------------------------------------------------------------

describe('useReportsMutations (updateReportVisibility)', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('updateReportVisibility calls PUT /api/v1/reports/:id/visibility', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          report_id: 'r1',
          name: 'My Report',
          current_version: 2,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-02T00:00:00Z',
          created_by: 'alice@example.com',
          updated_by: 'alice@example.com',
          access: { scope: 'public' },
          pinned: false,
        }),
    });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    await act(async () => {
      await result.current.updateReportVisibility('r1', 'public');
    });

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/visibility',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ access: { scope: 'public' } }),
      }),
    );
  });

  it('updateReportVisibility throws backend error on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ error: 'Report must be unpinned first' }),
    });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    await act(async () => {
      await expect(
        result.current.updateReportVisibility('r1', 'private'),
      ).rejects.toThrow('Report must be unpinned first');
    });
  });
});

// ---------------------------------------------------------------------------
// useReportsMutations – pinReport
// ---------------------------------------------------------------------------

describe('useReportsMutations (pinReport)', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('pinReport calls PUT /api/v1/reports/:id/pin with pinned true', async () => {
    mockFetch.mockResolvedValue({ ok: true });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    await act(async () => {
      await result.current.pinReport('r1', true);
    });

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/pin',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ pinned: true }),
      }),
    );
  });

  it('pinReport calls PUT /api/v1/reports/:id/pin with pinned false', async () => {
    mockFetch.mockResolvedValue({ ok: true });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    await act(async () => {
      await result.current.pinReport('r1', false);
    });

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1/pin',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ pinned: false }),
      }),
    );
  });

  it('pinReport throws on non-ok response', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 403 });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    await act(async () => {
      await expect(result.current.pinReport('r1', true)).rejects.toThrow();
    });
  });
});

// ---------------------------------------------------------------------------
// useReportsMutations – deleteReport
// ---------------------------------------------------------------------------

describe('useReportsMutations (deleteReport)', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('deleteReport throws backend error on non-ok response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: () =>
        Promise.resolve({
          error: 'Report must be unpinned before it can be deleted',
        }),
    });

    const { result } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });

    await act(async () => {
      await expect(result.current.deleteReport('r1')).rejects.toThrow(
        'Report must be unpinned before it can be deleted',
      );
    });
  });
});

// ---------------------------------------------------------------------------
// Shared fixture for useReport / useDashboardReport tests
// ---------------------------------------------------------------------------

const REPORT_CONFIG: Report = {
  name: 'My Report',
  rows: [
    {
      name: 'Row 1',
      panels: [
        {
          type: 'count',
          cypher: 'MATCH (n) RETURN count(n) AS total',
          caption: 'Total',
        },
      ],
    },
  ],
};

const REPORT_VERSION = {
  report_id: 'r1',
  name: 'My Report',
  version: 3,
  config: REPORT_CONFIG,
  created_at: '2024-01-01T00:00:00Z',
  created_by: 'alice@example.com',
  report_created_by: 'alice@example.com',
  report_updated_by: 'alice@example.com',
  access: { scope: 'public' as const },
  comment: null,
  query_capabilities: { 'tok-1': 'signed-abc' },
};

// ---------------------------------------------------------------------------
// useReport
// ---------------------------------------------------------------------------

describe('useReport', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    clearCapabilitiesCache();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('fetches and returns report data on first mount', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(REPORT_VERSION),
    });

    const { result } = renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.report).toEqual(REPORT_CONFIG);
    expect(result.current.name).toBe('My Report');
    expect(result.current.queryCapabilities).toEqual({ 'tok-1': 'signed-abc' });
    expect(result.current.error).toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/r1?include_query_capabilities=true',
      expect.any(Object),
    );
  });

  it('serves from cache on remount without fetching again', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(REPORT_VERSION),
    });

    // First mount populates the cache.
    const { result: first, unmount } = renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(false, null),
    });
    await waitFor(() => expect(first.current.loading).toBe(false));
    expect(mockFetch).toHaveBeenCalledTimes(1);
    unmount();

    // Second mount should read from cache: loading starts false, no new fetch.
    const { result: second } = renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(false, null),
    });
    expect(second.current.loading).toBe(false);
    expect(second.current.report).toEqual(REPORT_CONFIG);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('discards a malformed cached report and fetches the latest version', async () => {
    const malformedReport = {
      name: 'Malformed Report',
      panels: [{ type: 'markdown', content: 'Wrong level' }],
    } as unknown as Report;
    updateCachedReportCapabilities('r1', {
      report: malformedReport,
      name: 'Malformed Report',
      reportVersion: {
        ...REPORT_VERSION,
        version: 1,
        config: malformedReport,
      },
      queryCapabilities: {},
    });
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(REPORT_VERSION),
    });

    const { result } = renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(false, null),
    });

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.report).toEqual(REPORT_CONFIG);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('refresh() busts the cache and re-fetches', async () => {
    const v2 = {
      ...REPORT_VERSION,
      version: 4,
      query_capabilities: { 'tok-2': 'signed-xyz' },
    };
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(REPORT_VERSION),
      })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(v2) });

    const { result } = renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(false, null),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.queryCapabilities).toEqual({ 'tok-1': 'signed-abc' });

    act(() => {
      result.current.refresh();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.queryCapabilities).toEqual({ 'tok-2': 'signed-xyz' });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('does not fetch when reportId is undefined', () => {
    renderHook(() => useReport(undefined), {
      wrapper: makeWrapper(false, null),
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('does not fetch when auth_required and accessToken is null', () => {
    renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(true, null),
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('sets error when fetch returns non-ok status', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 404 });

    const { result } = renderHook(() => useReport('r1'), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.report).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// useDashboardReport
// ---------------------------------------------------------------------------

describe('useDashboardReport', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    clearCapabilitiesCache();
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  it('fetches and returns dashboard report on first mount', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(REPORT_VERSION),
    });

    const { result } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.report).toEqual(REPORT_CONFIG);
    expect(result.current.queryCapabilities).toEqual({ 'tok-1': 'signed-abc' });
    expect(result.current.notConfigured).toBe(false);
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/v1/reports/dashboard?include_query_capabilities=true',
      expect.any(Object),
    );
  });

  it('serves from cache on remount without fetching again', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(REPORT_VERSION),
    });

    const { result: first, unmount } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });
    await waitFor(() => expect(first.current.loading).toBe(false));
    expect(mockFetch).toHaveBeenCalledTimes(1);
    unmount();

    const { result: second } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });
    expect(second.current.loading).toBe(false);
    expect(second.current.report).toEqual(REPORT_CONFIG);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('setDashboardReport busts the cache so the next mount re-fetches', async () => {
    const v2 = {
      ...REPORT_VERSION,
      config: { ...REPORT_CONFIG, name: 'New Dashboard' },
    };
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(REPORT_VERSION),
      })
      .mockResolvedValueOnce({ ok: true }) // PUT .../dashboard
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(v2) });

    const { result: first, unmount } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });
    await waitFor(() => expect(first.current.loading).toBe(false));
    expect(first.current.report).toEqual(REPORT_CONFIG);
    unmount();

    const { result: mutations } = renderHook(() => useReportsMutations(), {
      wrapper: makeWrapper(false, null),
    });
    await act(async () => {
      await mutations.current.setDashboardReport('r2');
    });

    const { result: second } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });
    await waitFor(() => expect(second.current.loading).toBe(false));
    expect(second.current.report).toEqual(v2.config);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it('refresh() busts the cache and re-fetches', async () => {
    const v2 = {
      ...REPORT_VERSION,
      query_capabilities: { 'tok-new': 'signed-new' },
    };
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(REPORT_VERSION),
      })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(v2) });

    const { result } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.queryCapabilities).toEqual({ 'tok-1': 'signed-abc' });

    act(() => {
      result.current.refresh();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.queryCapabilities).toEqual({
      'tok-new': 'signed-new',
    });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('sets notConfigured when dashboard returns 404', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 404 });

    const { result } = renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(false, null),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notConfigured).toBe(true);
    expect(result.current.report).toBeUndefined();
  });

  it('does not fetch when auth_required and accessToken is null', () => {
    renderHook(() => useDashboardReport(), {
      wrapper: makeWrapper(true, null),
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
