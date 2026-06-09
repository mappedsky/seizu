import { useCallback, useEffect, useState } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

export interface ActionConfirmation {
  confirmation_id: string;
  source: 'mcp' | 'chat';
  tool_name: string;
  action: string;
  resource_type: string;
  resource_id: string;
  arguments: Record<string, unknown>;
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'executed';
  batch_id?: string | null;
  thread_id?: string | null;
  created_at: string;
  expires_at: string;
  decided_at?: string | null;
}

interface ConfirmationResponse {
  confirmation: ActionConfirmation;
}

/**
 * A pending confirmation whose expiry has passed can no longer be acted on, but
 * the server leaves its stored status as `pending` (expiry is time-based, see
 * `action_confirmations.is_expired`). Mirror that check client-side.
 */
export function isConfirmationExpired(
  confirmation: Pick<ActionConfirmation, 'expires_at'>,
): boolean {
  return new Date(confirmation.expires_at).getTime() <= Date.now();
}

/**
 * The status to show the user: a `pending` confirmation past its expiry reads as
 * `expired` so the UI hides Allow/Deny instead of offering a decision that the
 * backend will reject.
 */
export function effectiveConfirmationStatus(
  confirmation: Pick<ActionConfirmation, 'status' | 'expires_at'>,
): ActionConfirmation['status'] {
  if (
    confirmation.status === 'pending' &&
    isConfirmationExpired(confirmation)
  ) {
    return 'expired';
  }
  return confirmation.status;
}

interface ConfirmationListResponse {
  confirmations: ActionConfirmation[];
}

function sameConfirmations(
  current: ActionConfirmation[],
  next: ActionConfirmation[],
): boolean {
  if (current.length !== next.length) return false;
  return current.every((item, index) => {
    const other = next[index];
    return (
      other !== undefined &&
      item.confirmation_id === other.confirmation_id &&
      item.status === other.status &&
      item.expires_at === other.expires_at
    );
  });
}

export function useConfirmationsApi(threadId?: string | null): {
  confirmations: ActionConfirmation[];
  loading: boolean;
  error: string | null;
  fetchConfirmations: () => Promise<void>;
  getConfirmation: (confirmationId: string) => Promise<ActionConfirmation>;
  getConfirmationsByBatchId: (batchId: string) => Promise<ActionConfirmation[]>;
  decideConfirmation: (
    confirmationId: string,
    decision: 'approved' | 'denied',
  ) => Promise<ActionConfirmation>;
} {
  const { checkAuthReady, authHeaders } = useAuthHeaders();
  const [confirmations, setConfirmations] = useState<ActionConfirmation[]>([]);
  const [loading, setLoading] = useState(Boolean(threadId));
  const [error, setError] = useState<string | null>(null);

  const fetchConfirmations = useCallback(async () => {
    if (!threadId || !checkAuthReady()) {
      setConfirmations((prev) => (prev.length === 0 ? prev : []));
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const res = await fetch(
        `/api/v1/confirmations?thread_id=${encodeURIComponent(threadId)}`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error('Failed to fetch confirmations');
      const data = (await res.json()) as ConfirmationListResponse;
      setConfirmations((current) =>
        sameConfirmations(current, data.confirmations)
          ? current
          : data.confirmations,
      );
    } catch {
      setError('Failed to load confirmations.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, checkAuthReady, threadId]);

  const hasPending = confirmations.length > 0;

  useEffect(() => {
    if (!threadId) {
      setConfirmations((prev) => (prev.length === 0 ? prev : []));
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    void fetchConfirmations();
    const timer = window.setInterval(
      () => void fetchConfirmations(),
      hasPending ? 5000 : 30000,
    );
    return () => window.clearInterval(timer);
  }, [fetchConfirmations, hasPending, threadId]);

  const getConfirmation = useCallback(
    async (confirmationId: string): Promise<ActionConfirmation> => {
      const res = await fetch(
        `/api/v1/confirmations/${encodeURIComponent(confirmationId)}`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error('Failed to fetch confirmation');
      const data = (await res.json()) as ConfirmationResponse;
      return data.confirmation;
    },
    [authHeaders],
  );

  const getConfirmationsByBatchId = useCallback(
    async (batchId: string): Promise<ActionConfirmation[]> => {
      const res = await fetch(
        `/api/v1/confirmations/batch/${encodeURIComponent(batchId)}`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error('Failed to fetch batch confirmations');
      const data = (await res.json()) as ConfirmationListResponse;
      return data.confirmations;
    },
    [authHeaders],
  );

  const decideConfirmation = useCallback(
    async (
      confirmationId: string,
      decision: 'approved' | 'denied',
    ): Promise<ActionConfirmation> => {
      const res = await fetch(
        `/api/v1/confirmations/${encodeURIComponent(confirmationId)}/decision`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Seizu-Csrf': '1',
            ...authHeaders(),
          },
          body: JSON.stringify({ decision }),
        },
      );
      if (!res.ok) throw new Error('Failed to update confirmation');
      const data = (await res.json()) as ConfirmationResponse;
      setConfirmations((prev) =>
        prev.filter((item) => item.confirmation_id !== confirmationId),
      );
      return data.confirmation;
    },
    [authHeaders],
  );

  return {
    confirmations,
    loading,
    error,
    fetchConfirmations,
    getConfirmation,
    getConfirmationsByBatchId,
    decideConfirmation,
  };
}
