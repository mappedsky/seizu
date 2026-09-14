import { SeizuChatTransport } from 'src/api/chatTransport';
import type { UIMessage } from 'ai';

function buildTransport(threadId: string | null) {
  return new SeizuChatTransport<UIMessage>({
    threadId: () => threadId,
    accessToken: () => 'token-123',
    onStopFailed: jest.fn(),
    onUnresolvedChange: jest.fn(),
    admissionBody: () => ({}),
  });
}

function sseResponse() {
  return new Response('data: [DONE]\n\n', {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

/** What the transport did, without the SDK's own parsing of the body.
 *
 * The attach itself is what these tests are about. Handing the body to the SDK
 * is not: its stream transforms reject this environment's `ReadableStream`, so
 * a rejection from that point is not a statement about the transport.
 */
async function settle(attach: Promise<unknown>): Promise<unknown> {
  return attach.catch(() => 'stream-not-parsed');
}

describe('SeizuChatTransport', () => {
  let fetchMock: jest.SpyInstance;
  afterEach(() => {
    fetchMock.mockRestore();
  });

  // The landing shows its conversation as soon as the session exists, so the
  // reattach can arrive while the turn is still being admitted. Asking
  // `/turns/active` in that window answers 204 about a turn being admitted
  // right then, and the client is left with no id to attach to, stop or
  // recover — a conversation that looks like it never started.
  it('waits for an admission in flight instead of probing for a turn', async () => {
    let admit: (value: Response) => void = () => {};
    const admitted = new Promise<Response>((resolve) => {
      admit = resolve;
    });
    fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/turns')) return admitted;
        return sseResponse();
      });

    const transport = buildTransport('thread-new');
    const start = transport.startTurn('thread-new', 'Which repos are exposed?');
    // Handled from the moment it exists: the stream it hands back is parsed by
    // the SDK, and an unhandled rejection from that fails the run elsewhere.
    const reattach = settle(
      transport.reconnectToStream(
        {} as unknown as Parameters<
          SeizuChatTransport<UIMessage>['reconnectToStream']
        >[0],
      ),
    );

    // Nothing asks about a turn whose admission this client is holding, and
    // nothing attaches to one either: the reattach is waiting, not guessing.
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/active')),
    ).toBe(false);
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/stream')),
    ).toBe(false);

    admit(
      new Response(JSON.stringify({ turn_id: 'turn-new', status: 'created' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(start).resolves.toBe('turn-new');
    // Attached to the turn that admission answered with — not to whatever a
    // probe would have found, and not to nothing.
    expect(await reattach).not.toBeNull();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes('/turns/turn-new/stream'),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/active')),
    ).toBe(false);
  });

  // Whoever asked for the admission reports its failure; there is simply no
  // stream to hand back, and throwing here would report it a second time.
  it('has no stream to attach when the admission it waited on failed', async () => {
    fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/turns')) return new Response('', { status: 503 });
        return sseResponse();
      });

    const transport = buildTransport('thread-new');
    const start = transport.startTurn('thread-new', 'Which repos are exposed?');
    const reattach = transport.reconnectToStream(
      {} as unknown as Parameters<
        SeizuChatTransport<UIMessage>['reconnectToStream']
      >[0],
    );
    await expect(start).rejects.toThrow();
    expect(await reattach).toBeNull();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/stream')),
    ).toBe(false);
  });
});
