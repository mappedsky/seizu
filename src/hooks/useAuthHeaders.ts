import { useCallback, useContext, useRef } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';

export function useAuthHeaders(): {
  authReady: boolean;
  checkAuthReady: () => boolean;
  authHeaders: () => Record<string, string>;
} {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const authReady = !auth_required || Boolean(accessToken);
  const accessTokenRef = useRef(accessToken);
  accessTokenRef.current = accessToken;
  const authRequiredRef = useRef(auth_required);
  authRequiredRef.current = auth_required;

  const checkAuthReady = useCallback(
    () => !authRequiredRef.current || Boolean(accessTokenRef.current),
    [],
  );

  const authHeaders = useCallback((): Record<string, string> => {
    const headers: Record<string, string> = {};
    if (accessTokenRef.current) {
      headers.Authorization = `Bearer ${accessTokenRef.current}`;
    }
    return headers;
  }, []);

  return { authReady, checkAuthReady, authHeaders };
}
