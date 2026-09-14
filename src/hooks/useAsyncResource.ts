import { useEffect, useRef, useState } from 'react';

export interface AsyncResource<T> {
  data: T;
  loading: boolean;
  error: Error | null;
}

interface Settled<T> {
  key: string;
  data: T;
  error: Error | null;
}

/**
 * Builds a request key from the inputs a load reads. Serialised rather than
 * joined on a separator, so no value can be mistaken for a different split of
 * its neighbours and a number cannot collide with the string that spells it.
 */
export function resourceKey(
  ...parts: (string | number | boolean | null | undefined)[]
): string {
  return JSON.stringify(parts.map((part) => part ?? null));
}

/**
 * Loads a value whenever `key` changes, and reports it as `{ data, loading,
 * error }`.
 *
 * `loading` is *derived*: it means "the settled result is not an answer to the
 * request this render is asking for". Nothing has to set it on the way into an
 * effect, so a request that is superseded — a token refresh, a second mount,
 * StrictMode's double-invoke — cannot leave the flag pointing at a request
 * nobody is waiting for.
 *
 * `key` must name every input `load` reads **and everything that decides
 * whether it is `null`**, because the effect re-runs on `key` alone: a load
 * that becomes possible without moving the key is never started, and one whose
 * inputs moved without it stays closed over the old ones.
 *
 * - `key === null` means there is nothing to load: `loading` is false and
 *   `load` never runs.
 * - `load === null` with a non-null `key` means a load is owed but cannot start
 *   yet (typically: waiting for an access token). `loading` stays true and
 *   nothing is fetched; whatever unblocks it must change `key`.
 *
 * `data` keeps the previously settled value across a key change, so a refresh
 * does not blank the view it is refreshing; `error` is scoped to the current
 * key, so a new request clears the old failure without a second state write.
 */
export function useAsyncResource<T>(
  key: string | null,
  load: (() => Promise<T>) | null,
  initial: T,
): AsyncResource<T> {
  // Frozen on first render: callers pass literals like `[]`, and a fresh
  // identity every render would propagate into their consumers' deps.
  const initialRef = useRef(initial);
  const [settled, setSettled] = useState<Settled<T> | null>(null);

  useEffect(() => {
    if (key === null || load === null) return;
    let cancelled = false;
    load()
      .then((data) => {
        if (!cancelled) setSettled({ key, data, error: null });
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setSettled((previous) => ({
            key,
            // A failed request leaves what was on screen on screen, which is
            // what setting only the error state used to do.
            data: previous ? previous.data : initialRef.current,
            error,
          }));
        }
      });
    return () => {
      cancelled = true;
    };
    // `load` is deliberately not a dependency: the closure captured here
    // belongs to the render that produced this `key`, which is the one whose
    // inputs the key names.
  }, [key]);

  const current = settled !== null && settled.key === key ? settled : null;
  return {
    data: settled ? settled.data : initialRef.current,
    loading: key !== null && current === null,
    error: current ? current.error : null,
  };
}
