import { useCallback, useContext, useState } from 'react';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';

export interface GraphIndex {
  name: string;
  type: string;
  entity_type: string;
  labels_or_types: string[];
  properties: string[];
  state: string;
}

export interface GraphSchema {
  labels: string[];
  relationship_types: string[];
  property_keys: string[];
  indexes?: GraphIndex[];
}

interface GraphSchemaState {
  loading: boolean;
  error: Error | null;
  schema: GraphSchema | null;
}

export function useGraphSchema() {
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const [state, setState] = useState<GraphSchemaState>({
    loading: false,
    error: null,
    schema: null,
  });

  const fetchSchema = useCallback(() => {
    if (auth_required && !accessToken) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    const headers: Record<string, string> = {};
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    fetch('/api/v1/graph/schema', { headers })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: GraphSchema) => {
        setState({ loading: false, error: null, schema: data });
      })
      .catch((err: Error) => {
        setState({ loading: false, error: err, schema: null });
      });
  }, [accessToken, auth_required]);

  return { ...state, fetchSchema };
}
