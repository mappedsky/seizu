import { createContext, createElement, useContext, ReactNode } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { resourceKey, useAsyncResource } from 'src/hooks/useAsyncResource';

export interface CurrentUser {
  user_id: string;
  sub: string;
  iss: string;
  email: string | null;
  display_name: string | null;
  preferred_username?: string | null;
  created_at: string;
  last_login: string;
  archived_at: string | null;
  permissions: string[];
}

export interface CurrentUserState {
  currentUser: CurrentUser | null;
  loading: boolean;
}

// Shape of the actual /api/v1/me JSON response.
interface MeApiResponse {
  user: Omit<CurrentUser, 'permissions'>;
  permissions: string[];
}

const CurrentUserContext = createContext<CurrentUserState | undefined>(
  undefined,
);

function getApiHeaders(accessToken: string | null): Record<string, string> {
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

function useLoadCurrentUserState(enabled: boolean = true): CurrentUserState {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);

  const waitingForToken = auth_required && !accessToken;
  const { data, loading, error } = useAsyncResource<CurrentUser | null>(
    resourceKey('me', enabled, auth_required, accessToken),
    !enabled || waitingForToken
      ? null
      : async () => {
          const res = await fetch('/api/v1/me', {
            headers: getApiHeaders(accessToken),
          });
          if (!res.ok)
            throw new Error(`Failed to load current user: ${res.status}`);
          const body: MeApiResponse = await res.json();
          return { ...body.user, permissions: body.permissions };
        },
    null,
  );

  // A user the server would no longer confirm is not shown: losing the token,
  // or failing to resolve it, means nobody rather than whoever was here last.
  return {
    currentUser: waitingForToken || error !== null ? null : data,
    loading,
  };
}

export function CurrentUserStateProvider({
  value,
  children,
}: {
  value: CurrentUserState;
  children: ReactNode;
}) {
  return createElement(CurrentUserContext.Provider, { value }, children);
}

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const state = useLoadCurrentUserState();
  return createElement(CurrentUserContext.Provider, { value: state }, children);
}

export function useCurrentUserState(): CurrentUserState {
  const state = useContext(CurrentUserContext);
  const fallbackState = useLoadCurrentUserState(state === undefined);
  return state ?? fallbackState;
}

export function useCurrentUser(): CurrentUser | null {
  const { currentUser } = useCurrentUserState();
  return currentUser;
}
