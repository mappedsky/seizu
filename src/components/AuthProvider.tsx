import { ReactNode, useState, useEffect, useContext } from 'react';
import {
  beginLogin,
  refreshSession,
  type RefreshResponse,
} from 'src/api/authClient';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';

// Module-level dedup of in-flight refreshes. React StrictMode double-mounts
// effects in dev, which would fire two /auth/refresh calls in parallel — and
// since Authentik rotates refresh tokens, the second call would race the
// first and get 401 from a now-rotated cookie. Sharing one in-flight promise
// across concurrent callers eliminates the race *within one tab*.
let inflightRefresh: Promise<RefreshResponse> | null = null;

// Cross-tab serialization. The per-document promise above can't see other
// tabs, so two tabs whose proactive-refresh timers fire together would POST
// the *same* refresh token concurrently. With refresh-token rotation the IDP
// rotates on the first request and rejects the second as a replay — and
// reuse-detecting IDPs may revoke the entire token family, logging every tab
// out. The Web Locks API lets only one tab hold this named lock at a time, so
// the second tab waits until the first has rolled the session cookie and then
// refreshes against the new token. Where Web Locks is unavailable (older
// browsers, insecure contexts, the test env) we degrade to per-tab dedup.
const REFRESH_LOCK_NAME = 'seizu-auth-refresh';

function withCrossTabLock<T>(fn: () => Promise<T>): Promise<T> {
  const lockManager: LockManager | undefined =
    typeof navigator === 'undefined' ? undefined : navigator.locks;
  if (!lockManager) {
    return fn();
  }
  return lockManager.request(REFRESH_LOCK_NAME, fn) as Promise<T>;
}

function refreshOnce(): Promise<RefreshResponse> {
  if (!inflightRefresh) {
    inflightRefresh = withCrossTabLock(() => refreshSession()).finally(() => {
      inflightRefresh = null;
    });
  }
  return inflightRefresh;
}

// Reset hook for tests — concurrent module state would otherwise leak
// between test cases.
export function _resetInflightRefreshForTests(): void {
  inflightRefresh = null;
}

const REFRESH_LEAD_TIME_SECONDS = 30;
const REFRESH_LEAD_TIME_FLOOR_MS = 5_000;
const DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 300;
const LOGGED_OUT_PATH = '/logged-out';

interface AuthProviderProps {
  children: ReactNode;
}

function AuthProvider({ children }: AuthProviderProps) {
  const { auth_required, loaded } = useContext(AuthConfigContext);
  const [session, setSession] = useState<{ accessToken: string } | null>(null);

  // The logged-out page is a terminal state, not a session to bootstrap.
  const loggedOut = window.location.pathname === LOGGED_OUT_PATH;
  // Hold until the real config arrives. The auth_required:true default is
  // optimistic; bootstrapping a login flow against it would fire
  // /auth/refresh (401) and /auth/login (503) on a server with auth
  // disabled, before GET /api/v1/config reports auth_required:false.
  const needsSession = auth_required && loaded && !loggedOut;
  // Derived rather than a flag three branches of the effect have to lower:
  // "auth is required and we do not yet have the session it needs".
  const isLoading = auth_required && (!loaded || (needsSession && !session));

  useEffect(() => {
    if (!needsSession) return undefined;

    let cancelled = false;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;

    async function tick(): Promise<void> {
      try {
        const { access_token, expires_in } = await refreshOnce();
        if (cancelled) return;
        setSession({ accessToken: access_token });
        const ttl = expires_in ?? DEFAULT_ACCESS_TOKEN_TTL_SECONDS;
        const delay = Math.max(
          REFRESH_LEAD_TIME_FLOOR_MS,
          (ttl - REFRESH_LEAD_TIME_SECONDS) * 1000,
        );
        refreshTimer = setTimeout(() => {
          void tick();
        }, delay);
      } catch {
        if (cancelled) return;
        const returnTo =
          window.location.pathname +
          window.location.search +
          window.location.hash;
        try {
          const { authorize_url } = await beginLogin(returnTo);
          window.location.assign(authorize_url);
        } catch {
          // IDP unreachable — leave the user on the loading state. There's
          // no useful programmatic recovery from this; ops needs to fix the
          // IDP.
        }
      }
    }

    void tick();

    return () => {
      cancelled = true;
      if (refreshTimer !== null) clearTimeout(refreshTimer);
    };
  }, [needsSession]);

  const accessToken = loggedOut ? null : (session?.accessToken ?? null);

  return (
    <AuthContext.Provider value={{ accessToken, isLoading }}>
      {isLoading ? null : children}
    </AuthContext.Provider>
  );
}

export default AuthProvider;
