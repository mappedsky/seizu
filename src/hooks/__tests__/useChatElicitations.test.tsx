import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { useChatElicitations } from '../useChatElicitations';
import { AuthConfigContext } from 'src/authConfig.context';

afterEach(() => {
  cleanup();
  jest.restoreAllMocks();
});

it('hides consumed forms while keeping answered forms available for continuation', async () => {
  jest.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(
      JSON.stringify({
        elicitations: ['pending', 'accepted', 'consumed'].map((status) => ({
          elicitation_id: status,
          thread_id: 'thread-1',
          status,
        })),
      }),
      { status: 200 },
    ),
  );
  const { result } = renderHook(() => useChatElicitations('thread-1', false), {
    wrapper: ({ children }) => (
      <AuthConfigContext.Provider
        value={{ auth_required: false, oidc: null, loaded: true }}
      >
        {children}
      </AuthConfigContext.Provider>
    ),
  });
  await waitFor(() =>
    expect(result.current.items.map((item) => item.status)).toEqual([
      'pending',
      'accepted',
    ]),
  );
});
