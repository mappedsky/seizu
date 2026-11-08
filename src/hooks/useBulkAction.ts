import { useCallback, useState } from 'react';

export type BulkItemStatus = 'running' | 'done' | 'failed';

export interface BulkFailure<T> {
  item: T;
  message: string;
}

export interface BulkOutcome<T> {
  succeeded: T[];
  failed: BulkFailure<T>[];
}

export interface BulkAction<T> {
  /**
   * Apply `action` to each item in order, one at a time, then call `finalize`
   * exactly once — after the last item, whether or not any failed.
   *
   * Items run sequentially so the server sees one write at a time and a
   * failure names its item. Callers pass `notify: false` to their mutations
   * and reload in `finalize`, so mounted lists reload once per batch.
   */
  run: (
    items: T[],
    action: (item: T) => Promise<unknown>,
    finalize?: () => void,
  ) => Promise<BulkOutcome<T>>;
  /** Per-item state of the current or last batch, by key. */
  status: (item: T) => BulkItemStatus | undefined;
  /** `{ done, of }` while a batch runs, null otherwise. */
  progress: { done: number; of: number } | null;
  running: boolean;
  /** Forget the last batch's per-item states. */
  reset: () => void;
}

function messageOf(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return 'failed';
}

/**
 * Sequential batch runner with per-item status and a single finalize.
 *
 * `keyOf` must be stable (module-level or memoized): `run` and `status` are
 * rebuilt whenever it changes.
 */
export function useBulkAction<T>(keyOf: (item: T) => string): BulkAction<T> {
  const [statuses, setStatuses] = useState<Record<string, BulkItemStatus>>({});
  const [progress, setProgress] = useState<{ done: number; of: number } | null>(
    null,
  );
  const run = useCallback(
    async (
      items: T[],
      action: (item: T) => Promise<unknown>,
      finalize?: () => void,
    ): Promise<BulkOutcome<T>> => {
      const key = keyOf;
      const succeeded: T[] = [];
      const failed: BulkFailure<T>[] = [];
      setStatuses({});
      setProgress({ done: 0, of: items.length });
      try {
        for (const [index, item] of items.entries()) {
          setStatuses((prev) => ({ ...prev, [key(item)]: 'running' }));
          try {
            await action(item);
            succeeded.push(item);
            setStatuses((prev) => ({ ...prev, [key(item)]: 'done' }));
          } catch (err) {
            failed.push({ item, message: messageOf(err) });
            setStatuses((prev) => ({ ...prev, [key(item)]: 'failed' }));
          }
          setProgress({ done: index + 1, of: items.length });
        }
      } finally {
        setProgress(null);
        finalize?.();
      }
      return { succeeded, failed };
    },
    [keyOf],
  );

  const status = useCallback(
    (item: T) => statuses[keyOf(item)],
    [statuses, keyOf],
  );
  const reset = useCallback(() => setStatuses({}), []);

  return { run, status, progress, running: progress !== null, reset };
}
