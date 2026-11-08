import { act, renderHook } from '@testing-library/react';
import { useBulkAction } from 'src/hooks/useBulkAction';

interface Item {
  id: string;
}

const keyOf = (item: Item) => item.id;
const ITEMS: Item[] = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

describe('useBulkAction', () => {
  it('runs items one at a time, in order', async () => {
    const { result } = renderHook(() => useBulkAction(keyOf));
    const log: string[] = [];
    let inFlight = 0;

    await act(async () => {
      await result.current.run(ITEMS, async (item) => {
        inFlight += 1;
        expect(inFlight).toBe(1);
        log.push(`start ${item.id}`);
        await Promise.resolve();
        log.push(`end ${item.id}`);
        inFlight -= 1;
      });
    });

    expect(log).toEqual([
      'start a',
      'end a',
      'start b',
      'end b',
      'start c',
      'end c',
    ]);
  });

  it('finalizes exactly once, after the last item, even when items fail', async () => {
    const { result } = renderHook(() => useBulkAction(keyOf));
    const order: string[] = [];
    const finalize = jest.fn(() => order.push('finalize'));

    let outcome: Awaited<ReturnType<typeof result.current.run>> | undefined;
    await act(async () => {
      outcome = await result.current.run(
        ITEMS,
        async (item) => {
          order.push(item.id);
          if (item.id === 'b') throw new Error('boom');
        },
        finalize,
      );
    });

    expect(finalize).toHaveBeenCalledTimes(1);
    expect(order).toEqual(['a', 'b', 'c', 'finalize']);
    expect(outcome?.succeeded.map(keyOf)).toEqual(['a', 'c']);
    expect(outcome?.failed).toEqual([{ item: { id: 'b' }, message: 'boom' }]);
  });

  it('reports per-item status and clears progress when done', async () => {
    const { result } = renderHook(() => useBulkAction(keyOf));

    await act(async () => {
      await result.current.run(ITEMS, async (item) => {
        if (item.id === 'c') throw new Error('nope');
      });
    });

    expect(result.current.status({ id: 'a' })).toBe('done');
    expect(result.current.status({ id: 'c' })).toBe('failed');
    expect(result.current.status({ id: 'z' })).toBeUndefined();
    expect(result.current.progress).toBeNull();
    expect(result.current.running).toBe(false);

    act(() => result.current.reset());
    expect(result.current.status({ id: 'a' })).toBeUndefined();
  });
});
