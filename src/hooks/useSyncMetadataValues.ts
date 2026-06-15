import { useEffect, useState } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

export interface SyncMetadataValues {
  grouptypes: string[];
  syncedtypes: string[];
  groupids: string[];
}

const EMPTY: SyncMetadataValues = {
  grouptypes: [],
  syncedtypes: [],
  groupids: [],
};

/**
 * Distinct SyncMetadata grouptype/syncedtype/groupid values from the graph,
 * used as autocomplete options for watch-scan fields. Failures resolve to
 * empty lists — the fields still accept free-form input.
 */
export function useSyncMetadataValues(enabled: boolean): SyncMetadataValues {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [values, setValues] = useState<SyncMetadataValues>(EMPTY);

  useEffect(() => {
    let cancelled = false;
    if (!enabled || !checkAuthReady()) return undefined;
    fetch('/api/v1/sync-metadata/values', { headers: authHeaders() })
      .then((res) => {
        if (!res.ok) throw new Error('Failed to fetch sync metadata values');
        return res.json() as Promise<SyncMetadataValues>;
      })
      .then((data) => {
        if (!cancelled) {
          setValues({
            grouptypes: data.grouptypes ?? [],
            syncedtypes: data.syncedtypes ?? [],
            groupids: data.groupids ?? [],
          });
        }
      })
      .catch(() => {
        // Autocomplete is best-effort; typing remains available.
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, authHeaders, checkAuthReady]);

  return values;
}
