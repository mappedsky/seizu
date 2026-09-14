import {
  useState,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
} from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { usePermissionState } from 'src/hooks/usePermissions';

export type QueryRecord = Record<string, unknown>;

// Module-level cache for report query results, keyed by token+params.
// Survives component remounts (navigation, edit↔view mode switches).
// Only caches report queries (those with a reportToken); ad-hoc queries are never cached.
// Evicts oldest entry when the cap is reached; token expiry naturally busts the cache
// because the new token produces a different key.
const MAX_CACHE_ENTRIES = 200;
const queryResultCache = new Map<string, QueryRecord[]>();

function makeCacheKey(
  token: string,
  params: Record<string, unknown> | undefined,
): string {
  return `${token}|${JSON.stringify(params ?? {})}`;
}

function readQueryCache(
  token: string,
  params: Record<string, unknown> | undefined,
): QueryRecord[] | undefined {
  return queryResultCache.get(makeCacheKey(token, params));
}

function writeQueryCache(
  token: string,
  params: Record<string, unknown> | undefined,
  records: QueryRecord[],
): void {
  if (queryResultCache.size >= MAX_CACHE_ENTRIES) {
    const oldest = queryResultCache.keys().next().value;
    if (oldest !== undefined) queryResultCache.delete(oldest);
  }
  queryResultCache.set(makeCacheKey(token, params), records);
}

export function clearQueryResultCache(): void {
  queryResultCache.clear();
}

export interface QueryState {
  loading: boolean;
  error: Error | null;
  records: QueryRecord[] | undefined;
  first: QueryRecord | undefined;
  warnings: string[];
  queryErrors: string[];
  tokenExpired: boolean;
  historyId: string | null;
}

export interface RunOptions {
  /** When true, skip the cache and always fetch from the server. */
  force?: boolean;
}

const IDLE_STATE: QueryState = {
  loading: false,
  error: null,
  records: undefined,
  first: undefined,
  warnings: [],
  queryErrors: [],
  tokenExpired: false,
  historyId: null,
};

const DENIED_STATE: QueryState = {
  ...IDLE_STATE,
  error: new Error('You do not have permission to run this query.'),
};

/**
 * One `run()`. The sequence number is what makes a repeat of the same query a
 * new request, and what tells an arriving result whether it is still the
 * answer to the question being asked.
 *
 * The query and the token are captured here rather than read from the render
 * that happens to dispatch it. A request is for the cypher it was made
 * against: a panel whose `cypher` prop changes without running again has not
 * asked a new question, and the query console sets `cypher` to undefined when
 * it switches to a history query, which resent the last request with no
 * `query` field at all (a 422).
 */
interface QueryRequest {
  seq: number;
  cypher?: string;
  reportToken?: string;
  params?: Record<string, unknown>;
  options?: RunOptions;
}

interface QueryResponsePayload {
  error?: string;
  code?: string;
  errors?: string[];
  warnings?: string[];
  results?: QueryRecord[];
  history_id?: string;
}

/**
 * The state a request is in while it has no answer yet. Whatever the last
 * answer held stays visible, so a refresh or a token retry does not flash the
 * panel back to its skeleton.
 */
function pendingState(previous: QueryState | undefined): QueryState {
  return {
    ...(previous ?? IDLE_STATE),
    loading: true,
    error: null,
    warnings: [],
    queryErrors: [],
    tokenExpired: false,
  };
}

export function useLazyCypherQuery(
  cypher?: string,
  reportToken?: string,
): [
  (params?: Record<string, unknown>, options?: RunOptions) => void,
  QueryState,
] {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  // `run` records the request; the effect below is the only thing that starts
  // a query. A run that arrives before the token or the permission set did
  // used to be stashed in a ref and replayed by an effect calling `run` again,
  // which meant the effect wrote query state on the way in.
  const seqRef = useRef(0);
  const [request, setRequest] = useState<QueryRequest | null>(null);
  const [outcome, setOutcome] = useState<{
    seq: number;
    state: QueryState;
  } | null>(null);

  const run = useCallback(
    (params?: Record<string, unknown>, options?: RunOptions) => {
      if (!cypher) return;
      seqRef.current += 1;
      setRequest({ seq: seqRef.current, cypher, reportToken, params, options });
    },
    [cypher, reportToken],
  );

  // Read at dispatch rather than depended on: a token rotation must not resend
  // every panel's query behind its back. What makes a waiting request runnable
  // is `ready` below, which moves when the token first arrives.
  const accessTokenRef = useRef(accessToken);
  useEffect(() => {
    accessTokenRef.current = accessToken;
  });

  const ready = !(auth_required && !accessToken) && !permissionsLoading;
  const permitted = hasPermission(
    reportToken ? 'reports:read' : 'query:execute',
  );
  // A cache hit is an answer, not a fetch. Reading it here rather than in the
  // effect is what keeps a revisited panel from rendering a skeleton first.
  const cached =
    request !== null &&
    ready &&
    permitted &&
    request.reportToken &&
    !request.options?.force
      ? readQueryCache(request.reportToken, request.params)
      : undefined;

  useEffect(() => {
    if (request === null || !ready || !permitted) return undefined;
    if (cached !== undefined) return undefined;

    const {
      seq,
      params,
      cypher: requestCypher,
      reportToken: requestToken,
    } = request;
    if (!requestCypher && !requestToken) return undefined;
    let cancelled = false;
    const settle = (state: QueryState) => {
      if (!cancelled) setOutcome({ seq, state });
    };

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (accessTokenRef.current) {
      headers['Authorization'] = `Bearer ${accessTokenRef.current}`;
    }

    const endpoint = requestToken
      ? '/api/v1/query/report'
      : '/api/v1/query/adhoc';
    const body = requestToken
      ? { token: requestToken, params }
      : { query: requestCypher, params };

    fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    })
      .then((res) => res.json())
      .then((data: QueryResponsePayload) => {
        const validationErrors = data.errors ?? [];
        const validationWarnings = data.warnings ?? [];

        if (data.error) {
          if (data.code === 'token_expired') {
            // Keep records/first from the previous answer so panels continue
            // showing stale data while the token refresh and retry run.
            if (cancelled) return;
            setOutcome((previous) => ({
              seq,
              state: {
                ...(previous?.state ?? IDLE_STATE),
                loading: false,
                error: null,
                tokenExpired: true,
              },
            }));
            return;
          }
          // Server or request-level error (HTTP 500, malformed request, etc.)
          settle({ ...IDLE_STATE, error: new Error(data.error) });
          return;
        }

        if (validationErrors.length > 0) {
          // Query validation errors — the query was not executed.
          settle({
            ...IDLE_STATE,
            warnings: validationWarnings,
            queryErrors: validationErrors,
          });
          return;
        }

        const results = data.results ?? [];
        if (requestToken) {
          writeQueryCache(requestToken, params, results);
        }
        settle({
          ...IDLE_STATE,
          records: results,
          first: results[0],
          warnings: validationWarnings,
          historyId: data.history_id ?? null,
        });
      })
      .catch((err: Error) => {
        settle({ ...IDLE_STATE, error: err });
      });

    return () => {
      cancelled = true;
    };
  }, [request, ready, permitted, cached]);

  const state = useMemo<QueryState>(() => {
    if (request === null) return IDLE_STATE;
    if (outcome !== null && outcome.seq === request.seq) return outcome.state;
    if (ready && !permitted) return DENIED_STATE;
    if (cached !== undefined) {
      return { ...IDLE_STATE, records: cached, first: cached[0] };
    }
    return pendingState(outcome?.state);
  }, [request, outcome, ready, permitted, cached]);

  return [run, state];
}

export function useLazyHistoryQuery(): [
  (historyId: string) => void,
  QueryState,
] {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const seqRef = useRef(0);
  const [request, setRequest] = useState<{
    seq: number;
    historyId: string;
  } | null>(null);
  const [outcome, setOutcome] = useState<{
    seq: number;
    state: QueryState;
  } | null>(null);

  const run = useCallback((historyId: string) => {
    if (!historyId) return;
    seqRef.current += 1;
    setRequest({ seq: seqRef.current, historyId });
  }, []);

  // As above: read at dispatch, not depended on, so a token rotation does not
  // resend the request.
  const accessTokenRef = useRef(accessToken);
  useEffect(() => {
    accessTokenRef.current = accessToken;
  });

  const ready = !(auth_required && !accessToken) && !permissionsLoading;
  const permitted = hasPermission('query:execute');

  useEffect(() => {
    if (request === null || !ready || !permitted) return undefined;

    const { seq, historyId } = request;
    let cancelled = false;
    const settle = (state: QueryState) => {
      if (!cancelled) setOutcome({ seq, state });
    };

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (accessTokenRef.current) {
      headers['Authorization'] = `Bearer ${accessTokenRef.current}`;
    }

    fetch('/api/v1/query/history', {
      method: 'POST',
      headers,
      body: JSON.stringify({ history_id: historyId }),
    })
      .then((res) => res.json())
      .then((data: QueryResponsePayload) => {
        const validationErrors = data.errors ?? [];
        const validationWarnings = data.warnings ?? [];

        if (data.error) {
          settle({ ...IDLE_STATE, error: new Error(data.error) });
          return;
        }
        if (validationErrors.length > 0) {
          settle({
            ...IDLE_STATE,
            warnings: validationWarnings,
            queryErrors: validationErrors,
          });
          return;
        }
        const results = data.results ?? [];
        settle({
          ...IDLE_STATE,
          records: results,
          first: results[0],
          warnings: validationWarnings,
        });
      })
      .catch((err: Error) => {
        settle({ ...IDLE_STATE, error: err });
      });

    return () => {
      cancelled = true;
    };
  }, [request, ready, permitted]);

  const state = useMemo<QueryState>(() => {
    if (request === null) return IDLE_STATE;
    if (outcome !== null && outcome.seq === request.seq) return outcome.state;
    if (ready && !permitted) return DENIED_STATE;
    return pendingState(outcome?.state);
  }, [request, outcome, ready, permitted]);

  return [run, state];
}
