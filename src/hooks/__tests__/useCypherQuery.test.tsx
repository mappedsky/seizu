import { renderHook, act, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import {
  useLazyCypherQuery,
  useLazyHistoryQuery,
  clearQueryResultCache,
} from 'src/hooks/useCypherQuery';
import {
  CurrentUser,
  CurrentUserState,
  CurrentUserStateProvider,
} from 'src/hooks/useCurrentUser';
import { usePermissionState } from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

const CYPHER = 'MATCH (n) RETURN n';

const AUTH_CONFIG_NO_OIDC = {
  auth_required: false,
  oidc: null,
  loaded: true,
};
const CURRENT_USER: CurrentUser = {
  user_id: 'user-1',
  sub: 'sub-1',
  iss: 'issuer',
  email: 'dev@example.com',
  display_name: 'Developer',
  created_at: '2025-01-01T00:00:00Z',
  last_login: '2025-01-01T00:00:00Z',
  archived_at: null,
  permissions: ['query:execute', 'reports:read'],
};

const mockUsePermissionState = usePermissionState as jest.MockedFunction<
  typeof usePermissionState
>;

function makeWrapper(
  authRequired: boolean,
  accessToken: string | null,
  currentUserState: CurrentUserState = {
    currentUser: CURRENT_USER,
    loading: false,
  },
) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <AuthConfigContext.Provider
        value={{ ...AUTH_CONFIG_NO_OIDC, auth_required: authRequired }}
      >
        <AuthContext.Provider value={{ accessToken, isLoading: false }}>
          <CurrentUserStateProvider value={currentUserState}>
            {children}
          </CurrentUserStateProvider>
        </AuthContext.Provider>
      </AuthConfigContext.Provider>
    );
  };
}

// Stateful wrapper that allows updating auth values after initial render.
let _setToken: ((t: string | null) => void) | null = null;
function StatefulWrapper({ children }: { children: React.ReactNode }) {
  const [accessToken, setToken] = useState<string | null>(null);
  _setToken = setToken;
  return (
    <AuthConfigContext.Provider
      value={{ ...AUTH_CONFIG_NO_OIDC, auth_required: true }}
    >
      <AuthContext.Provider value={{ accessToken, isLoading: false }}>
        <CurrentUserStateProvider
          value={{ currentUser: CURRENT_USER, loading: false }}
        >
          {children}
        </CurrentUserStateProvider>
      </AuthContext.Provider>
    </AuthConfigContext.Provider>
  );
}

describe('useLazyCypherQuery', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: CURRENT_USER,
    });
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: [] }),
    });
  });

  it('does not fetch when auth_required and accessToken is null', () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(true, null),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('does not fetch while permissions are loading', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: true,
      currentUser: null,
    });
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null, { currentUser: null, loading: true }),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('does not fetch when the user lacks query permission', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: false,
      currentUser: { ...CURRENT_USER, permissions: ['reports:read'] },
    });
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null, {
        currentUser: { ...CURRENT_USER, permissions: ['reports:read'] },
        loading: false,
      }),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(result.current[1].error?.message).toBe(
      'You do not have permission to run this query.',
    );
  });

  it('fetches with Authorization header when auth_required and accessToken is set', () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(true, 'mytoken'),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/query/adhoc',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer mytoken' }),
      }),
    );
  });

  it('fetches without Authorization header when auth not required', () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    expect(global.fetch).toHaveBeenCalled();
    const headers = (global.fetch as jest.Mock).mock.calls[0][1].headers;
    expect(headers).not.toHaveProperty('Authorization');
  });

  it('includes params in the request body', () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    const [run] = result.current;
    act(() => {
      run({ base_severity: 'CRITICAL', count: 3 });
    });
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.query).toBe(CYPHER);
    expect(body.params).toEqual({ base_severity: 'CRITICAL', count: 3 });
  });

  it('sends query without params when run is called with no arguments', () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.query).toBe(CYPHER);
    expect(body.params).toBeUndefined();
  });

  it('replays a blocked query when accessToken becomes available', async () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: StatefulWrapper,
    });
    act(() => {
      result.current[0]();
    });
    expect(global.fetch).not.toHaveBeenCalled();

    // Simulate token arriving by updating the stateful wrapper's state.
    act(() => {
      _setToken!('newtoken');
    });
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });
  });

  it('replays a blocked query when permissions finish loading', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: true,
      currentUser: CURRENT_USER,
    });
    const { result, rerender } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]();
    });
    expect(global.fetch).not.toHaveBeenCalled();

    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: CURRENT_USER,
    });
    rerender();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });
  });

  it('omits save_history for ad hoc queries', () => {
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    const [run] = result.current;
    act(() => {
      run();
    });
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.save_history).toBeUndefined();
  });

  it('uses the signed report endpoint when a report token is provided', () => {
    const { result } = renderHook(
      () => useLazyCypherQuery(CYPHER, 'signed-token'),
      {
        wrapper: makeWrapper(false, null),
      },
    );
    const [run] = result.current;
    act(() => {
      run({ base_severity: 'HIGH' });
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/query/report',
      expect.objectContaining({
        body: JSON.stringify({
          token: 'signed-token',
          params: { base_severity: 'HIGH' },
        }),
      }),
    );
  });

  it('exposes historyId from the server response on success', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () =>
        Promise.resolve({ results: [{ n: 1 }], history_id: 'hist-abc' }),
    });
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].historyId).toBe('hist-abc');
  });

  it('sets historyId to null when the response omits history_id', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: [] }),
    });
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].historyId).toBeNull();
  });

  it('exposes Neo4j execution errors as queryErrors', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () =>
        Promise.resolve({
          errors: ['Invalid call signature for datetime()'],
          warnings: ['Planner warning'],
        }),
    });
    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].queryErrors).toEqual([
      'Invalid call signature for datetime()',
    ]);
    expect(result.current[1].warnings).toEqual(['Planner warning']);
    expect(result.current[1].error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// useLazyHistoryQuery
// ---------------------------------------------------------------------------

describe('useLazyHistoryQuery', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: CURRENT_USER,
    });
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: [] }),
    });
  });

  it('does not fetch when auth_required and accessToken is null', () => {
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(true, null),
    });
    const [run] = result.current;
    act(() => {
      run('hist-1');
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('does not fetch while permissions are loading', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: true,
      currentUser: null,
    });
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null, { currentUser: null, loading: true }),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('replays a blocked history query when permissions finish loading', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: true,
      currentUser: CURRENT_USER,
    });
    const { result, rerender } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    expect(global.fetch).not.toHaveBeenCalled();

    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: CURRENT_USER,
    });
    rerender();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });
  });

  it('does not fetch without query:execute permission', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: false,
      currentUser: { ...CURRENT_USER, permissions: [] },
    });
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null, {
        currentUser: { ...CURRENT_USER, permissions: [] },
        loading: false,
      }),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(result.current[1].error?.message).toBe(
      'You do not have permission to run this query.',
    );
  });

  it('POSTs to /api/v1/query/history with the history_id', () => {
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-xyz');
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/query/history',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ history_id: 'hist-xyz' }),
      }),
    );
  });

  it('returns results on success', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: [{ name: 'Alice' }] }),
    });
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].records).toEqual([{ name: 'Alice' }]);
    expect(result.current[1].queryErrors).toEqual([]);
  });

  it('sets error state on server error response', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ error: 'Not found' }),
    });
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-missing');
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].error?.message).toBe('Not found');
    expect(result.current[1].records).toBeUndefined();
  });

  it('sets queryErrors on validation error response', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () =>
        Promise.resolve({
          errors: ['Write queries are not allowed'],
          warnings: [],
        }),
    });
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-bad');
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].queryErrors).toEqual([
      'Write queries are not allowed',
    ]);
    expect(result.current[1].error).toBeNull();
  });

  it('historyId is always null (re-execution does not create new history)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () =>
        Promise.resolve({
          results: [{ n: 1 }],
          history_id: 'should-be-ignored',
        }),
    });
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].historyId).toBeNull();
  });

  it('includes Authorization header when accessToken is set', () => {
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(true, 'tok-abc'),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/query/history',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer tok-abc' }),
      }),
    );
  });

  it('sets error state on network failure', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('Network error'));
    const { result } = renderHook(() => useLazyHistoryQuery(), {
      wrapper: makeWrapper(false, null),
    });
    act(() => {
      result.current[0]('hist-1');
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].error?.message).toBe('Network error');
  });
});

// ---------------------------------------------------------------------------
// Query result cache
// ---------------------------------------------------------------------------

describe('query result cache', () => {
  const RECORDS = [{ name: 'Alice' }, { name: 'Bob' }];
  const TOKEN = 'report-token-cache-tests';

  beforeEach(() => {
    jest.clearAllMocks();
    clearQueryResultCache();
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: CURRENT_USER,
    });
  });

  it('serves cached results on second call without fetching again', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: RECORDS }),
    });

    const { result } = renderHook(() => useLazyCypherQuery(CYPHER, TOKEN), {
      wrapper: makeWrapper(false, null),
    });

    act(() => {
      result.current[0]({ severity: 'HIGH' });
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].records).toEqual(RECORDS);
    expect(global.fetch).toHaveBeenCalledTimes(1);

    // Second call with same token+params should hit the cache.
    act(() => {
      result.current[0]({ severity: 'HIGH' });
    });
    await waitFor(() => expect(result.current[1].records).toEqual(RECORDS));
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('bypasses cache and fetches when force: true', async () => {
    const freshRecords = [{ name: 'Charlie' }];
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ results: RECORDS }),
      })
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ results: freshRecords }),
      });

    const { result } = renderHook(
      () => useLazyCypherQuery(CYPHER, TOKEN + '-force'),
      {
        wrapper: makeWrapper(false, null),
      },
    );

    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(global.fetch).toHaveBeenCalledTimes(1);

    // force: true should bypass the cache and fetch again.
    act(() => {
      result.current[0](undefined, { force: true });
    });
    await waitFor(() =>
      expect(result.current[1].records).toEqual(freshRecords),
    );
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it('never caches ad-hoc queries (no reportToken)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: RECORDS }),
    });

    const { result } = renderHook(() => useLazyCypherQuery(CYPHER), {
      wrapper: makeWrapper(false, null),
    });

    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));

    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));

    // Both calls should have hit the network — no caching without a reportToken.
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it('sets tokenExpired and preserves existing records on token_expired response', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ results: RECORDS }),
      })
      .mockResolvedValueOnce({
        json: () =>
          Promise.resolve({
            error: 'Token has expired',
            code: 'token_expired',
          }),
      });

    const { result } = renderHook(
      () => useLazyCypherQuery(CYPHER, TOKEN + '-expiry'),
      {
        wrapper: makeWrapper(false, null),
      },
    );

    // First call succeeds and populates records.
    act(() => {
      result.current[0]();
    });
    await waitFor(() => expect(result.current[1].records).toEqual(RECORDS));
    expect(result.current[1].tokenExpired).toBe(false);

    // Second call (force to bypass cache) receives token_expired.
    act(() => {
      result.current[0](undefined, { force: true });
    });
    await waitFor(() => expect(result.current[1].tokenExpired).toBe(true));

    // Records from before the expiry are preserved so panels keep showing data.
    expect(result.current[1].records).toEqual(RECORDS);
    expect(result.current[1].loading).toBe(false);
  });

  it('different params produce separate cache entries', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ results: [{ n: 1 }] }),
      })
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ results: [{ n: 2 }] }),
      });

    const { result } = renderHook(
      () => useLazyCypherQuery(CYPHER, TOKEN + '-params'),
      {
        wrapper: makeWrapper(false, null),
      },
    );

    act(() => {
      result.current[0]({ severity: 'HIGH' });
    });
    await waitFor(() => expect(result.current[1].loading).toBe(false));
    expect(result.current[1].records).toEqual([{ n: 1 }]);

    act(() => {
      result.current[0]({ severity: 'LOW' });
    });
    await waitFor(() => expect(result.current[1].records).toEqual([{ n: 2 }]));

    // Both params combinations fetched from network.
    expect(global.fetch).toHaveBeenCalledTimes(2);

    // Repeat HIGH — served from cache, no third fetch.
    act(() => {
      result.current[0]({ severity: 'HIGH' });
    });
    await waitFor(() => expect(result.current[1].records).toEqual([{ n: 1 }]));
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });
});
