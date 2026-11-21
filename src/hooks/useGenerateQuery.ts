import { useCallback } from 'react';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';
import { resourceKey, useAsyncResource } from 'src/hooks/useAsyncResource';
import type { ReasoningEffort } from 'src/hooks/useModelProfilesApi';

/** A profile the console's Ask mode may generate with. */
export interface QueryConsoleModelOption {
  profile_id: string;
  name: string;
  description: string;
  model_id: string;
  default_reasoning_effort: ReasoningEffort;
  reasoning_efforts: ReasoningEffort[];
}

/** What `GET /api/v1/query/generate/options` returns. */
export interface QueryConsoleModelOptions {
  /** False when profiles exist but none is enabled for the console. */
  available: boolean;
  /** Empty when the deployment has no profiles and its own settings apply. */
  profiles: QueryConsoleModelOption[];
  default_profile_id: string | null;
}

const NO_OPTIONS: QueryConsoleModelOptions = {
  available: true,
  profiles: [],
  default_profile_id: null,
};

/**
 * The profiles and reasoning levels Ask mode offers. Until they load, Ask is
 * assumed available with nothing to pick; a failed load leaves it that way,
 * and generating then reports what is wrong.
 */
export function useQueryConsoleModels(enabled: boolean): {
  options: QueryConsoleModelOptions;
  loading: boolean;
} {
  const { authReady, authHeaders } = useAuthHeaders();
  const { data, loading } = useAsyncResource<QueryConsoleModelOptions>(
    enabled ? resourceKey(authReady) : null,
    authReady
      ? async () => {
          const response = await fetch('/api/v1/query/generate/options', {
            headers: authHeaders(),
          });
          if (!response.ok) return NO_OPTIONS;
          return (await response.json()) as QueryConsoleModelOptions;
        }
      : null,
    NO_OPTIONS,
  );
  return { options: data, loading };
}

/** What `POST /api/v1/query/generate` returns for a question. */
export interface GeneratedQuery {
  query: string;
  explanation: string;
  errors: string[];
  warnings: string[];
  /**
   * Set when the query validated and was saved to history with the question
   * as its prompt; the console runs it by this id.
   */
  history_id: string | null;
  /** The model that answered, the reasoning effort sent, and its profile. */
  model_id: string;
  reasoning_effort: string;
  profile_name: string;
}

export interface GenerateQueryOptions {
  currentQuery: string;
  /** Omitted to use the default profile at its default reasoning level. */
  modelProfileId?: string;
  reasoningEffort?: string;
  signal?: AbortSignal;
}

export type GenerateQueryOutcome =
  | { ok: true; result: GeneratedQuery }
  | { ok: false; error: string };

const UNAVAILABLE = 'Query generation is not available right now.';

/**
 * Ask the chat model for a Cypher query answering `question`. `currentQuery`
 * is what the editor holds, so a follow-up can refine it. Resolves to an
 * outcome rather than throwing, so the caller has one shape to render.
 */
export function useGenerateQuery(): (
  question: string,
  options: GenerateQueryOptions,
) => Promise<GenerateQueryOutcome> {
  const { checkAuthReady, authHeaders } = useAuthHeaders();

  return useCallback(
    async (
      question: string,
      {
        currentQuery,
        modelProfileId,
        reasoningEffort,
        signal,
      }: GenerateQueryOptions,
    ): Promise<GenerateQueryOutcome> => {
      if (!checkAuthReady()) {
        return { ok: false, error: 'You are not signed in.' };
      }
      try {
        const res = await fetch('/api/v1/query/generate', {
          method: 'POST',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify({
            question,
            current_query: currentQuery,
            ...(modelProfileId
              ? {
                  model_profile_id: modelProfileId,
                  reasoning_effort: reasoningEffort,
                }
              : {}),
          }),
          signal,
        });
        if (res.status === 403) {
          return {
            ok: false,
            error: 'You do not have permission to generate queries.',
          };
        }
        if (res.status === 503) {
          // Names what is wrong with the chosen model profile, when it is that.
          const body = (await res.json().catch(() => null)) as {
            error?: unknown;
          } | null;
          return {
            ok: false,
            error:
              typeof body?.error === 'string' && body.error
                ? body.error
                : UNAVAILABLE,
          };
        }
        if (!res.ok) {
          return {
            ok: false,
            error:
              res.status === 504
                ? 'The model took too long to answer. Try a narrower question.'
                : 'The query could not be generated.',
          };
        }
        return { ok: true, result: (await res.json()) as GeneratedQuery };
      } catch {
        return { ok: false, error: UNAVAILABLE };
      }
    },
    [authHeaders, checkAuthReady],
  );
}
