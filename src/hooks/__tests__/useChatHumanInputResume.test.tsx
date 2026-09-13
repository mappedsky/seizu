import { cleanup, renderHook } from '@testing-library/react';
import {
  type ChatHumanInputResume,
  useChatHumanInputResume,
} from 'src/hooks/useChatHumanInputResume';

afterEach(cleanup);

describe('useChatHumanInputResume', () => {
  for (const kind of ['confirmation', 'elicitation'] as const) {
    it(`dispatches an ID-only ${kind} continuation`, async () => {
      const send = jest.fn().mockResolvedValue(undefined);
      const touch = jest.fn();
      const { result } = renderHook(() =>
        useChatHumanInputResume('thread-1', send, touch),
      );
      await result.current({ kind, id: 'input-1', threadId: 'thread-1' });
      expect(touch).toHaveBeenCalledWith('thread-1');
      expect(send).toHaveBeenCalledTimes(1);
      expect(send).toHaveBeenCalledWith(
        {
          id: 'resume-input-1',
          role: 'user',
          metadata: { seizu_hidden: true },
          parts: [],
        },
        { body: { [`resume_${kind}_id`]: 'input-1' } },
      );
    });

    it(`rejects a delayed ${kind} resume after navigation`, async () => {
      const send = jest.fn();
      const touch = jest.fn();
      const { result, rerender } = renderHook(
        ({ threadId }) => useChatHumanInputResume(threadId, send, touch),
        { initialProps: { threadId: 'thread-1' } },
      );
      const originalResume = result.current;
      rerender({ threadId: 'thread-2' });
      await expect(
        originalResume({ kind, id: 'input-1', threadId: 'thread-1' }),
      ).rejects.toThrow('original conversation');
      expect(send).not.toHaveBeenCalled();
      expect(touch).not.toHaveBeenCalled();
    });
  }

  it('rejects a resume without an active conversation', async () => {
    const send = jest.fn();
    const { result } = renderHook(() =>
      useChatHumanInputResume(null, send, jest.fn()),
    );
    await expect(
      result.current({
        kind: 'elicitation',
        id: 'input-1',
        threadId: 'thread-1',
      }),
    ).rejects.toThrow('original conversation');
    expect(send).not.toHaveBeenCalled();
  });

  it('rejects a delayed resume after leaving chat', async () => {
    const send = jest.fn();
    const { result, unmount } = renderHook(() =>
      useChatHumanInputResume('thread-1', send, jest.fn()),
    );
    const resume = result.current;
    unmount();
    await expect(
      resume({
        kind: 'elicitation',
        id: 'input-1',
        threadId: 'thread-1',
      }),
    ).rejects.toThrow('original conversation');
    expect(send).not.toHaveBeenCalled();
  });

  it('propagates failure without retrying or retaining a previous resume ID', async () => {
    const send = jest.fn().mockRejectedValueOnce(new Error('admission failed'));
    const { result } = renderHook(() =>
      useChatHumanInputResume('thread-1', send, jest.fn()),
    );
    const request: ChatHumanInputResume = {
      kind: 'confirmation',
      id: 'approval-1',
      threadId: 'thread-1',
    };
    await expect(result.current(request)).rejects.toThrow('admission failed');
    expect(send).toHaveBeenCalledTimes(1);
    await result.current({ ...request, kind: 'elicitation', id: 'input-2' });
    expect(send).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'resume-input-2' }),
      { body: { resume_elicitation_id: 'input-2' } },
    );
  });

  it('propagates synchronous send failures as rejected promises', async () => {
    const send = jest.fn(() => {
      throw new Error('send failed');
    });
    const { result } = renderHook(() =>
      useChatHumanInputResume('thread-1', send, jest.fn()),
    );
    await expect(
      result.current({
        kind: 'confirmation',
        id: 'approval-1',
        threadId: 'thread-1',
      }),
    ).rejects.toThrow('send failed');
    expect(send).toHaveBeenCalledTimes(1);
  });
});
