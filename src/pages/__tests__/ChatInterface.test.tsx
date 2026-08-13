import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ChatInterface from 'src/pages/ChatInterface';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { FeaturesContext, DEFAULT_FEATURES } from 'src/features.context';
import * as usePermissionsModule from 'src/hooks/usePermissions';
import * as useChatHistoryModule from 'src/hooks/useChatHistory';
import * as useChatSessionsModule from 'src/hooks/useChatSessions';
import * as useConfirmationsApiModule from 'src/hooks/useConfirmationsApi';
import { useChat } from '@ai-sdk/react';
import { type ChatOnFinishCallback, type UIMessage } from 'ai';
import { SeizuChatTransport } from 'src/api/chatTransport';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/hooks/useChatHistory', () => ({
  useChatHistory: jest.fn(),
}));

jest.mock('src/hooks/useChatSessions', () => ({
  useChatSessions: jest.fn(),
}));

jest.mock('src/hooks/useConfirmationsApi', () => ({
  useConfirmationsApi: jest.fn(),
}));

jest.mock('@ai-sdk/react', () => ({
  useChat: jest.fn(),
}));

const mockUsePermissionState =
  usePermissionsModule.usePermissionState as jest.MockedFunction<
    typeof usePermissionsModule.usePermissionState
  >;
const mockUseChatHistory =
  useChatHistoryModule.useChatHistory as jest.MockedFunction<
    typeof useChatHistoryModule.useChatHistory
  >;
const mockUseChatSessions =
  useChatSessionsModule.useChatSessions as jest.MockedFunction<
    typeof useChatSessionsModule.useChatSessions
  >;
const mockUseConfirmationsApi =
  useConfirmationsApiModule.useConfirmationsApi as jest.MockedFunction<
    typeof useConfirmationsApiModule.useConfirmationsApi
  >;
const mockUseChat = useChat as jest.MockedFunction<typeof useChat>;

/** The transport the component handed to `useChat` on its last render.
 *
 * Its response parsing is stubbed to a passthrough: jsdom's `Response.body` is
 * not a real `ReadableStream`, and what these tests are about is which requests
 * the transport makes, not the SDK's own parser.
 */
function activeTransport(): SeizuChatTransport<UIMessage> {
  // useChat's options are a union whose other arm takes a prebuilt Chat, so
  // `transport` is not on the common type.
  const options = mockUseChat.mock.calls.at(-1)?.[0] as
    | { transport?: unknown }
    | undefined;
  if (!options?.transport) throw new Error('missing transport');
  const transport = options.transport as SeizuChatTransport<UIMessage>;
  jest
    .spyOn(
      transport as unknown as {
        processResponseStream: (s: unknown) => unknown;
      },
      'processResponseStream',
    )
    .mockImplementation((stream) => stream);
  return transport;
}

/** A transport whose thread the test controls, stubbed the same way
 * `activeTransport` stubs the component's: jsdom's `Response.body` is not a
 * real `ReadableStream`, and these tests are about which requests get made. */
function standaloneTransport(
  threadId: () => string | null,
  onUnresolvedChange: (
    threadId: string,
    unresolved: boolean,
  ) => void = () => {},
) {
  const transport = new SeizuChatTransport<UIMessage>({
    threadId,
    accessToken: () => 'token',
    onStopFailed: () => {},
    onUnresolvedChange,
    admissionBody: () => ({ message: 'Hi' }),
  });
  jest
    .spyOn(
      transport as unknown as {
        processResponseStream: (s: unknown) => unknown;
      },
      'processResponseStream',
    )
    .mockImplementation((stream) => stream);
  return transport;
}

/** Stub `fetch` for the admit-then-attach pair a send makes. */
function mockAdmitThenAttach(turnId = 'turn-42') {
  return jest.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/turns') && !url.includes('/stream')) {
      return new Response(
        JSON.stringify({ turn_id: turnId, status: 'created' }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    }
    return new Response('data: [DONE]\n\n', { status: 200 });
  });
}

function sendArgs(messages: UIMessage[]) {
  return {
    chatId: 'chat-id',
    messageId: messages.at(-1)?.id,
    messages,
    abortSignal: undefined,
    trigger: 'submit-message',
  } as unknown as Parameters<SeizuChatTransport<UIMessage>['sendMessages']>[0];
}
const theme = createTheme();

type ChatRenderOptions = {
  accessToken?: string | null;
  chatEnabled?: boolean;
  initialPath?: string;
};

function chatTree({
  accessToken = 'token-123',
  chatEnabled = true,
  initialPath = '/app/chat',
}: ChatRenderOptions = {}) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthConfigContext.Provider
        value={{
          auth_required: accessToken !== null,
          oidc: null,
          loaded: true,
        }}
      >
        <FeaturesContext.Provider
          value={{ ...DEFAULT_FEATURES, chat: chatEnabled }}
        >
          <AuthContext.Provider value={{ accessToken, isLoading: false }}>
            <ThemeProvider theme={theme}>
              <Routes>
                <Route path="/app/chat" element={<ChatInterface />} />
                <Route path="/app/chat/:threadId" element={<ChatInterface />} />
              </Routes>
            </ThemeProvider>
          </AuthContext.Provider>
        </FeaturesContext.Provider>
      </AuthConfigContext.Provider>
    </MemoryRouter>
  );
}

function renderChat(options: ChatRenderOptions = {}) {
  return render(chatTree(options));
}

describe('ChatInterface', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    mockUseChatHistory.mockReturnValue(() => Promise.resolve([]));
    mockUseChatSessions.mockReturnValue({
      sessions: [
        {
          thread_id: 'thread-1',
          title: 'Session 1',
          created_at: '2024-01-01T00:00:00+00:00',
          updated_at: '2024-01-01T00:00:00+00:00',
        },
      ],
      loading: false,
      error: null,
      createSession: jest.fn(),
      getSession: jest.fn().mockResolvedValue(null),
      updateSession: jest.fn(),
      deleteSession: jest.fn(),
      touchSession: jest.fn(),
    });
    mockUseConfirmationsApi.mockReturnValue({
      confirmations: [],
      loading: false,
      error: null,
      fetchConfirmations: jest.fn().mockResolvedValue(undefined),
      getConfirmation: jest.fn(),
      getConfirmationsByBatchId: jest.fn(),
      decideConfirmation: jest.fn(),
    });
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'chat:use',
      loading: false,
      currentUser: null,
    });
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    jest.useRealTimers();
    // Restores every spy, `globalThis.fetch` included. Per-test `mockRestore`
    // is not enough on its own: a failing assertion skips it, and the spy then
    // outlives this file -- which strands other suites in a shared process,
    // with the failure surfacing in whichever file runs next rather than here.
    jest.restoreAllMocks();
  });

  it('persists the active session id and configures the chat stream request body', async () => {
    renderChat();
    await act(async () => {}); // flush the on-mount history fetch

    const threadId = window.localStorage.getItem('seizu:chat:active-session');
    expect(threadId).toBe('thread-1');

    await waitFor(() => {
      expect(mockUseChat).toHaveBeenCalledWith(
        expect.objectContaining({
          id: threadId,
          experimental_throttle: 50,
          transport: expect.any(Object),
        }),
      );
    });

    const fetchMock = mockAdmitThenAttach();
    await activeTransport().sendMessages(
      sendArgs([
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Hello graph' }],
        },
      ]),
    );

    // Two requests, in order: ask for the turn, then read it.
    const [admitUrl, admitInit] = fetchMock.mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(admitUrl).toBe(`/api/v1/chat/threads/${threadId}/turns`);
    expect(admitInit.method).toBe('POST');
    expect(admitInit.headers).toEqual(
      expect.objectContaining({
        Authorization: 'Bearer token-123',
        'X-Seizu-Csrf': '1',
      }),
    );
    const body = JSON.parse(String(admitInit.body)) as {
      message: string;
      idempotency_key: string;
      thread_id?: string;
    };
    expect(body.message).toBe('Hello graph');
    // The thread is in the path now, so it has no business in the body too.
    expect(body.thread_id).toBeUndefined();
    expect(body.idempotency_key).toMatch(/^ik_/);

    const [attachUrl] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(attachUrl).toBe('/api/v1/chat/turns/turn-42/stream');
    fetchMock.mockRestore();
  });

  it('uses the latest access token when preparing chat stream requests', async () => {
    const { rerender } = renderChat({ accessToken: 'token-1' });
    await act(async () => {});

    rerender(
      <MemoryRouter initialEntries={['/app/chat']}>
        <AuthConfigContext.Provider
          value={{
            auth_required: true,
            oidc: null,
            loaded: true,
          }}
        >
          <FeaturesContext.Provider value={{ ...DEFAULT_FEATURES, chat: true }}>
            <AuthContext.Provider
              value={{ accessToken: 'token-2', isLoading: false }}
            >
              <ThemeProvider theme={theme}>
                <Routes>
                  <Route path="/app/chat" element={<ChatInterface />} />
                  <Route
                    path="/app/chat/:threadId"
                    element={<ChatInterface />}
                  />
                </Routes>
              </ThemeProvider>
            </AuthContext.Provider>
          </FeaturesContext.Provider>
        </AuthConfigContext.Provider>
      </MemoryRouter>,
    );

    const fetchMock = mockAdmitThenAttach();
    await activeTransport().sendMessages(
      sendArgs([
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Fresh token please' }],
        },
      ]),
    );

    const [, admitInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((admitInit.headers as Record<string, string>).Authorization).toBe(
      'Bearer token-2',
    );
    fetchMock.mockRestore();
  });

  it('shows a disabled message when the chat feature is off', () => {
    const fetchHistory = jest.fn().mockResolvedValue([]);
    mockUseChatHistory.mockReturnValue(fetchHistory);

    renderChat({ chatEnabled: false });

    expect(screen.getByText('Chat is not enabled.')).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText('Ask about your security graph...'),
    ).not.toBeInTheDocument();
    expect(fetchHistory).not.toHaveBeenCalled();
  });

  it('rehydrates persisted history into the chat on mount', async () => {
    const history = [
      {
        id: 'h1',
        role: 'user' as const,
        parts: [{ type: 'text' as const, text: 'Earlier question' }],
      },
      {
        id: 'h2',
        role: 'assistant' as const,
        parts: [{ type: 'text' as const, text: 'Earlier answer' }],
      },
    ];
    const fetchHistory = jest.fn().mockResolvedValue(history);
    mockUseChatHistory.mockReturnValue(fetchHistory);
    const setMessages = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages,
      clearError: jest.fn(),
    });

    renderChat();

    await waitFor(() => {
      expect(fetchHistory).toHaveBeenCalledWith('thread-1');
      expect(setMessages).toHaveBeenCalledTimes(1);
    });
    expect(setMessages).toHaveBeenCalledWith(history);
  });

  it('uses a linked session from the route', async () => {
    renderChat({ initialPath: '/app/chat/thread-1' });

    await waitFor(() => {
      expect(mockUseChat).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'thread-1' }),
      );
    });
    expect(window.localStorage.getItem('seizu:chat:active-session')).toBe(
      'thread-1',
    );
  });

  it('resumes a confirmation from the linked chat URL once', async () => {
    const sendMessage = jest.fn();
    const touchSession = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });
    mockUseChatSessions.mockReturnValue({
      sessions: [
        {
          thread_id: 'thread-1',
          title: 'Session 1',
          created_at: '2024-01-01T00:00:00+00:00',
          updated_at: '2024-01-01T00:00:00+00:00',
        },
      ],
      loading: false,
      error: null,
      createSession: jest.fn(),
      getSession: jest.fn().mockResolvedValue(null),
      updateSession: jest.fn(),
      deleteSession: jest.fn(),
      touchSession,
    });

    renderChat({
      initialPath: '/app/chat/thread-1?resume_confirmation_id=confirm-1',
    });

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'resume-confirm-1',
          role: 'user',
          metadata: { seizu_hidden: true },
          parts: [],
        }),
        {
          body: { resume_confirmation_id: 'confirm-1' },
        },
      );
    });
    expect(touchSession).toHaveBeenCalledWith('thread-1');
  });

  it('shows an error when resuming an approved confirmation fails', async () => {
    const sendMessage = jest.fn().mockRejectedValue(new Error('resume failed'));
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({
      initialPath: '/app/chat/thread-1?resume_confirmation_id=confirm-1',
    });

    await waitFor(() => {
      expect(
        screen.getByText('Failed to resume the approved confirmation.'),
      ).toBeInTheDocument();
    });
  });

  it('refreshes confirmations once when an approval-required response finishes', async () => {
    const fetchConfirmations = jest.fn().mockResolvedValue(undefined);
    mockUseConfirmationsApi.mockReturnValue({
      confirmations: [],
      loading: false,
      error: null,
      fetchConfirmations,
      getConfirmation: jest.fn(),
      getConfirmationsByBatchId: jest.fn(),
      decideConfirmation: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });

    await waitFor(() => {
      expect(mockUseChat).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'thread-1' }),
      );
    });

    const chatOptions = mockUseChat.mock.calls.at(-1)?.[0] as
      | { onFinish?: ChatOnFinishCallback<UIMessage> }
      | undefined;
    chatOptions?.onFinish?.({
      message: {
        id: 'approval-message',
        role: 'assistant',
        parts: [
          {
            type: 'text',
            text: 'Seizu needs your approval before running this action.',
          },
        ],
      },
      messages: [],
      isAbort: false,
      isDisconnect: false,
      isError: false,
      finishReason: 'stop',
    });

    await waitFor(() => {
      expect(fetchConfirmations).toHaveBeenCalledTimes(1);
    });
  });

  it('shows a not-found state for a missing linked session', async () => {
    renderChat({ initialPath: '/app/chat/missing-session' });

    expect(
      await screen.findByText('Chat session not found.'),
    ).toBeInTheDocument();
  });

  it('hydrates over a stale local user turn when history has caught up', async () => {
    const history = [
      {
        id: 'h1',
        role: 'user' as const,
        parts: [{ type: 'text' as const, text: 'Earlier question' }],
      },
    ];
    const fetchHistory = jest.fn().mockResolvedValue(history);
    mockUseChatHistory.mockReturnValue(fetchHistory);
    const setMessages = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'local-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Already typing' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages,
      clearError: jest.fn(),
    });

    renderChat();

    await waitFor(() => {
      expect(fetchHistory).toHaveBeenCalled();
      expect(setMessages).toHaveBeenCalledWith(history);
    });
  });

  it('sends typed input through useChat', async () => {
    const sendMessage = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    fireEvent.change(
      screen.getByPlaceholderText('Ask about your security graph...'),
      {
        target: { value: 'Map my graph' },
      },
    );
    const sendButton = screen.getByRole('button', { name: 'Send' });
    expect(
      screen
        .getByPlaceholderText('Ask about your security graph...')
        .closest('.MuiInputBase-root'),
    ).toContainElement(sendButton);
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: 'Map my graph' });
    });
  });

  it('sends typed input with Enter', async () => {
    const sendMessage = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    const input = screen.getByPlaceholderText(
      'Ask about your security graph...',
    );
    await act(async () => {
      fireEvent.change(input, { target: { value: 'Map my graph' } });
      fireEvent.keyDown(input, { key: 'Enter' });
    });

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: 'Map my graph' });
    });
  });

  it('keeps Shift+Enter available for multiline input', async () => {
    const sendMessage = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    const input = screen.getByPlaceholderText(
      'Ask about your security graph...',
    );
    await act(async () => {
      fireEvent.change(input, { target: { value: 'Line one' } });
      fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    });

    expect(sendMessage).not.toHaveBeenCalled();
  });

  it('renders streamed assistant text parts', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [{ type: 'text', text: 'Streaming response' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'streaming',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {}); // flush the on-mount history fetch

    expect(screen.getByText('Assistant')).toBeInTheDocument();
    expect(screen.getByText('Streaming response')).toBeInTheDocument();
    expect(screen.getByText('Assistant is working...')).toBeInTheDocument();
  });

  it('renders assistant detail data parts in a collapsed details block', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            {
              type: 'data-seizu-detail',
              id: 'detail-1',
              data: {
                kind: 'tool',
                title: 'Tool: graph__schema',
                status: 'completed',
                arguments: '{}',
                body: '{"labels":["CVE"]}',
              },
            },
            { type: 'text', text: 'Schema has CVEs.' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    expect(screen.getByText('Details')).toBeInTheDocument();
    expect(screen.getByText('Schema has CVEs.')).toBeInTheDocument();
    expect(screen.getByText('Tool: graph__schema')).not.toBeVisible();
    expect(screen.queryByText('{}')).not.toBeInTheDocument();
    expect(screen.queryByText('{"labels":["CVE"]}')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Details 1' }));
    const toolDetails = await screen.findByRole(
      'button',
      { name: /Tool: graph__schema/ },
      { timeout: 10_000 },
    );
    expect(toolDetails).toBeVisible();
    fireEvent.click(toolDetails);

    await waitFor(
      () => {
        expect(screen.getByText('{}')).toBeVisible();
        expect(screen.getByText('{"labels":["CVE"]}')).toBeVisible();
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('keeps details expanded when the response completes, collapses on manual click', async () => {
    const messages = [
      {
        id: 'assistant-message',
        role: 'assistant' as const,
        parts: [
          {
            type: 'data-seizu-detail' as const,
            id: 'detail-1',
            data: {
              kind: 'tool',
              title: 'Tool: graph__schema',
              status: 'completed',
              arguments: '{}',
              body: '{"labels":["CVE"]}',
            },
          },
          { type: 'text' as const, text: 'Schema has CVEs.' },
        ],
      },
    ];
    const chatResult = (status: 'streaming' | 'ready') => ({
      id: 'chat-id',
      messages,
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status,
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    mockUseChat.mockReturnValue(chatResult('streaming'));
    const { rerender } = renderChat();
    await act(async () => {});

    expect(screen.getByRole('button', { name: 'Details 1' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    // When streaming ends the panel must stay open so the user can read details
    // without having to manually re-expand it.
    mockUseChat.mockReturnValue(chatResult('ready'));
    rerender(chatTree());

    await act(async () => {});
    expect(screen.getByRole('button', { name: 'Details 1' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    // The user can still collapse the panel manually.
    fireEvent.click(screen.getByRole('button', { name: 'Details 1' }));
    expect(screen.getByRole('button', { name: 'Details 1' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('renders orchestration detail parts (plan/step/verify) in the details block', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            {
              type: 'data-seizu-detail',
              id: 'detail-routing',
              data: {
                kind: 'routing',
                title: 'Routing',
                status: 'completed',
              },
            },
            {
              type: 'data-seizu-detail',
              id: 'detail-plan',
              data: { kind: 'plan', title: 'Plan', status: 'completed' },
            },
            {
              type: 'data-seizu-detail',
              id: 'detail-step',
              data: {
                kind: 'step',
                title: 'Step: gather data',
                status: 'completed',
              },
            },
            {
              type: 'data-seizu-detail',
              id: 'detail-verify',
              data: {
                kind: 'verify',
                title: 'Verify: gather data',
                status: 'completed',
              },
            },
            { type: 'text', text: 'Synthesized answer.' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    // All four orchestration kinds are surfaced (count chip = 4).
    expect(screen.getByText('Synthesized answer.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details 4' }));
    await waitFor(
      () => {
        expect(screen.getByText('Routing')).toBeVisible();
        expect(screen.getByText('Plan')).toBeVisible();
        expect(screen.getByText('Step: gather data')).toBeVisible();
        expect(screen.getByText('Verify: gather data')).toBeVisible();
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('nests a subagent (with its inner tools) under an orchestrator step', async () => {
    // Orchestrator reload shape: a subagent tool detail carries step_id (nests it
    // under the step) AND children (its inner tool calls). The tree must render two
    // levels deep — step -> subagent -> inner tools — not flatten the inner rows.
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            {
              type: 'data-seizu-detail',
              id: 'detail-step',
              data: {
                kind: 'step',
                title: 'Step: crunch data',
                status: 'completed',
                step_id: 's1',
              },
            },
            {
              type: 'data-seizu-detail',
              id: 'tc-sandbox-1',
              data: {
                kind: 'subagent',
                title: 'Tool: sandbox__delegate',
                status: 'completed',
                step_id: 's1',
                detail_id: 'tc-sandbox-1',
                body: 'done',
                children: [
                  {
                    kind: 'tool',
                    title: 'Sandbox: run_python',
                    status: 'completed',
                    detail_id: 'sandbox-run-python',
                    body: '42',
                  },
                ],
              },
            },
            { type: 'text', text: 'Synthesized answer.' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    expect(screen.getByText('Synthesized answer.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details 2' }));
    await waitFor(
      () => {
        expect(screen.getByText('Step: crunch data')).toBeVisible();
        expect(screen.getByText('Tool: sandbox__delegate')).toBeVisible();
        // The inner tool (grandchild) is still rendered, not flattened away.
        expect(screen.getByText('Sandbox: run_python')).toBeInTheDocument();
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('groups Sandbox: sub-events under their parent Tool: sandbox__delegate entry', async () => {
    // The outer chat agent pre-emits a "running" detail for sandbox__delegate
    // (id="tc-sandbox-1") before the batch runs, then updates it to "completed"
    // after.  Inner sandbox sub-events carry parent_id="tc-sandbox-1" so they
    // are grouped under the outer entry.
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            // Outer tool running → completed (same SSE id, last value wins)
            {
              type: 'data-seizu-detail',
              id: 'tc-sandbox-1',
              data: {
                kind: 'tool',
                title: 'Tool: sandbox__delegate',
                status: 'completed',
                detail_id: 'tc-sandbox-1',
                arguments: '{"task":"run some code"}',
                body: 'done',
              },
            },
            // Inner sandbox sub-events (arrive after the running event)
            {
              type: 'data-seizu-detail',
              id: 'sandbox-run-python',
              data: {
                kind: 'tool',
                title: 'Sandbox: run_python',
                status: 'completed',
                detail_id: 'sandbox-run-python',
                parent_id: 'tc-sandbox-1',
                body: 'hello world',
              },
            },
            {
              type: 'data-seizu-detail',
              id: 'sandbox-run-bash',
              data: {
                kind: 'tool',
                title: 'Sandbox: run_bash',
                status: 'completed',
                detail_id: 'sandbox-run-bash',
                parent_id: 'tc-sandbox-1',
                body: 'exit 0',
              },
            },
            { type: 'text', text: 'All done.' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    // The chip shows the raw event count (3); grouping is structural, not counted.
    expect(screen.getByText('All done.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details 3' }));
    await waitFor(
      () => {
        expect(screen.getByText('Tool: sandbox__delegate')).toBeVisible();
        // Sandbox sub-events are children of the outer row, not root nodes.
        expect(screen.getByText('Sandbox: run_python')).toBeInTheDocument();
        expect(screen.getByText('Sandbox: run_bash')).toBeInTheDocument();
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('deduplicates running+completed events with the same detail_id (upsert semantics)', async () => {
    // The AI SDK appends both the "running" and "completed" events as separate
    // parts when they share the same SSE id.  buildDetailTree must upsert-by-
    // detail_id so the entry transitions in-place (running→completed) without
    // creating a duplicate node or losing its children.
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            // D1 running (pre-emitted before the batch)
            {
              type: 'data-seizu-detail',
              id: 'tc-sandbox-1',
              data: {
                kind: 'tool',
                title: 'Tool: sandbox__delegate',
                status: 'running',
                detail_id: 'tc-sandbox-1',
              },
            },
            // Inner tool running then completed (both appended as separate parts)
            {
              type: 'data-seizu-detail',
              id: 'sandbox-run-python',
              data: {
                kind: 'tool',
                title: 'Sandbox: run_python',
                status: 'running',
                detail_id: 'sandbox-run-python',
                parent_id: 'tc-sandbox-1',
              },
            },
            {
              type: 'data-seizu-detail',
              id: 'sandbox-run-python',
              data: {
                kind: 'tool',
                title: 'Sandbox: run_python',
                status: 'completed',
                detail_id: 'sandbox-run-python',
                parent_id: 'tc-sandbox-1',
                body: 'hello world',
              },
            },
            // D1 completed (appended, same detail_id as running)
            {
              type: 'data-seizu-detail',
              id: 'tc-sandbox-1',
              data: {
                kind: 'tool',
                title: 'Tool: sandbox__delegate',
                status: 'completed',
                detail_id: 'tc-sandbox-1',
                arguments: '{"task":"run some code"}',
                body: 'done',
              },
            },
            { type: 'text', text: 'All done.' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    // 4 raw detail events; chip reflects the raw count.
    expect(screen.getByText('All done.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details 4' }));
    await waitFor(
      () => {
        // Upsert collapses running+completed for sandbox__delegate into one node.
        const outerEntries = screen.getAllByText('Tool: sandbox__delegate');
        expect(outerEntries).toHaveLength(1);
        // The single entry should reflect the final "completed" state.
        expect(screen.getByText('Tool: sandbox__delegate')).toBeVisible();
        // The inner tool's running+completed collapse into one child entry.
        expect(screen.getAllByText('Sandbox: run_python')).toHaveLength(1);
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('renders a subagent detail with its inline children nested', async () => {
    // The current (reload-safe) shape: one detail entry of kind "subagent" whose
    // inner tool calls live in `children`. This is what a persisted sandbox turn
    // looks like after a reload — a single part, no sibling rows to reorder/lose.
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            {
              type: 'data-seizu-detail',
              id: 'tc-sandbox-1',
              data: {
                kind: 'subagent',
                title: 'Tool: sandbox__delegate',
                status: 'completed',
                detail_id: 'tc-sandbox-1',
                arguments: 'task: run some code',
                body: 'all done',
                children: [
                  {
                    kind: 'tool',
                    title: 'Sandbox: run_python',
                    status: 'completed',
                    detail_id: 'sandbox-run-python',
                    body: 'hello world',
                  },
                  {
                    kind: 'tool',
                    title: 'Sandbox: run_bash',
                    status: 'completed',
                    detail_id: 'sandbox-run-bash',
                    body: 'exit 0',
                  },
                ],
              },
            },
            { type: 'text', text: 'All done.' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    expect(screen.getByText('All done.')).toBeInTheDocument();
    // One top-level detail (the subagent section); children are structural.
    fireEvent.click(screen.getByRole('button', { name: 'Details 1' }));
    await waitFor(
      () => {
        expect(screen.getByText('Tool: sandbox__delegate')).toBeVisible();
        expect(screen.getByText('Sandbox: run_python')).toBeInTheDocument();
        expect(screen.getByText('Sandbox: run_bash')).toBeInTheDocument();
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('grows a subagent section in place as inner tool calls stream', async () => {
    // While streaming, the section is re-emitted under one id with a growing
    // children array. The latest frame wins (upsert by detail_id), so the section
    // appears immediately and fills in — it never duplicates or drops children.
    const sectionFrame = (children: unknown[]) => ({
      type: 'data-seizu-detail' as const,
      id: 'tc-sandbox-1',
      data: {
        kind: 'subagent',
        title: 'Tool: sandbox__delegate',
        status: 'running',
        detail_id: 'tc-sandbox-1',
        children,
      },
    });

    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            // The AI SDK may append each re-emitted frame as a separate part; the
            // newest frame (most children) must win after the upsert.
            sectionFrame([
              {
                kind: 'tool',
                title: 'Sandbox: run_python',
                status: 'running',
                detail_id: 'sandbox-run-python',
              },
            ]),
            sectionFrame([
              {
                kind: 'tool',
                title: 'Sandbox: run_python',
                status: 'completed',
                detail_id: 'sandbox-run-python',
                body: 'hello world',
              },
              {
                kind: 'tool',
                title: 'Sandbox: run_bash',
                status: 'running',
                detail_id: 'sandbox-run-bash',
              },
            ]),
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'streaming',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    await waitFor(
      () => {
        // Section present immediately; both children from the latest frame show,
        // and only once (no duplication from the earlier frame).
        expect(screen.getByText('Tool: sandbox__delegate')).toBeVisible();
        expect(screen.getAllByText('Sandbox: run_python')).toHaveLength(1);
        expect(screen.getByText('Sandbox: run_bash')).toBeInTheDocument();
      },
      { timeout: 10_000 },
    );
  }, 15_000);

  it('formats settled blocks as Markdown and leaves the in-progress block plain', async () => {
    // A block followed by a blank line is settled and parsed once; the still-
    // growing final block stays plain text (raw markup) until it settles. This is
    // what keeps streaming incremental — settled blocks never re-parse or flicker.
    const streamedText = ['# Findings', '', '**bold** in progress'].join('\n');
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [{ type: 'text', text: streamedText }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'streaming',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    // Settled block parsed into Markdown.
    expect(
      screen.getByRole('heading', { name: 'Findings' }),
    ).toBeInTheDocument();
    // In-progress block stays plain (raw markup visible, no <strong>).
    expect(screen.getByText(/\*\*bold\*\* in progress/)).toBeInTheDocument();
  });

  it('keeps an open code fence in the plain-text tail without mis-parsing it', async () => {
    // A fenced code block that straddles a blank line is still open, so it must
    // stay in the in-progress tail rather than being split into broken blocks.
    const streamedText = ['Intro paragraph.', '', '```python', 'x = 1'].join(
      '\n',
    );
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [{ type: 'text', text: streamedText }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'streaming',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    // The settled prose block renders as Markdown.
    expect(screen.getByText('Intro paragraph.')).toBeInTheDocument();
    // The open fence streams verbatim in the tail (backticks visible).
    expect(screen.getByText(/```python/)).toBeInTheDocument();
  });

  it('renders assistant responses with Markdoc in untrusted URL mode', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            {
              type: 'text',
              text: [
                '# Findings',
                '',
                '- **Critical** issue',
                '',
                '<script>alert(1)</script>',
                '',
                '[external app](slack://channel/T01)',
                '',
                '[safe](https://example.com/report)',
              ].join('\n'),
            },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    const { container } = renderChat();
    await act(async () => {});

    expect(
      screen.getByRole('heading', { name: 'Findings', level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText('Critical')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByRole('link', { name: 'external app' })).toHaveAttribute(
      'href',
      '#',
    );
    expect(screen.getByRole('link', { name: 'safe' })).toHaveAttribute(
      'href',
      'https://example.com/report',
    );
  });

  it('copies the unrendered assistant response text', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const rawResponse = [
      '# Findings',
      '',
      '- **Critical** issue',
      '',
      '[safe](https://example.com/report)',
    ].join('\n');
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Show findings' }],
        },
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [{ type: 'text', text: rawResponse }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    expect(
      screen.getAllByRole('button', { name: 'Copy assistant response' }),
    ).toHaveLength(1);
    fireEvent.click(
      screen.getByRole('button', { name: 'Copy assistant response' }),
    );

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(rawResponse);
    });
  });

  it('shows persisted timestamps and copies the user message from its hover actions', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const asked = '2024-05-05T10:20:00.000Z';
    const answered = '2024-05-05T10:20:30.000Z';
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          metadata: { created_at: asked },
          parts: [{ type: 'text', text: 'Show findings' }],
        },
        {
          id: 'assistant-message',
          role: 'assistant',
          metadata: { created_at: answered },
          parts: [{ type: 'text', text: 'Here they are' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    expect(
      screen.getByText(new Date(asked).toLocaleString()),
    ).toBeInTheDocument();
    expect(
      screen.getByText(new Date(answered).toLocaleString()),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Copy your message' }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('Show findings');
    });
  });

  it('leaves a persisted turn from before timestamps existed untimed', async () => {
    // Regression: stamping every untimed message with the browser clock dated
    // every message of every pre-existing conversation to today.
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          metadata: { seizu_persisted: true },
          parts: [{ type: 'text', text: 'Asked last year' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    const actions = screen
      .getByRole('button', { name: 'Copy your message' })
      .closest('[aria-label="User message actions"]');
    expect(actions).not.toBeNull();
    expect(actions?.textContent).not.toContain(
      new Date().getFullYear().toString(),
    );
  });

  it('stamps a live message that has no server timestamp yet', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Just sent' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    const actions = screen
      .getByRole('button', { name: 'Copy your message' })
      .closest('[aria-label="User message actions"]');
    expect(actions).not.toBeNull();
    // Rendered from the browser clock, so assert against today rather than a
    // fixed value.
    expect(actions?.textContent).toContain(
      new Date().toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'numeric',
        year: 'numeric',
      }),
    );
  });

  it('shows load more on output-limited assistant responses', async () => {
    const sendMessage = jest.fn();
    const touchSession = jest.fn();
    mockUseChatSessions.mockReturnValue({
      sessions: [
        {
          thread_id: 'thread-1',
          title: 'Session 1',
          created_at: '2024-01-01T00:00:00+00:00',
          updated_at: '2024-01-01T00:00:00+00:00',
        },
      ],
      loading: false,
      error: null,
      createSession: jest.fn(),
      getSession: jest.fn().mockResolvedValue(null),
      updateSession: jest.fn(),
      deleteSession: jest.fn(),
      touchSession,
    });
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          metadata: { finish_reason: 'length', response_cut_off: true },
          parts: [{ type: 'text', text: 'Partial response' }],
        },
      ],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    fireEvent.click(screen.getByRole('button', { name: 'Load more response' }));

    expect(touchSession).toHaveBeenCalledWith('thread-1');
    expect(sendMessage).toHaveBeenCalledWith(undefined, {
      body: {
        continue_message_id: 'assistant-message',
        continue_response: true,
      },
    });
  });

  it('renders the continuation divider from Markdoc markup', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Earlier question' }],
        },
        {
          id: 'assistant-message',
          role: 'assistant',
          parts: [
            {
              type: 'text',
              text: 'Partial response\n\n{% continuation /%}\n\n',
            },
            { type: 'text', text: 'continued answer' },
          ],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    expect(screen.getByText('Partial response')).toBeInTheDocument();
    expect(screen.getByText('continued answer')).toBeInTheDocument();
    expect(screen.getByText('...')).toBeInTheDocument();
    expect(screen.queryByText('{% continuation /%}')).not.toBeInTheDocument();
  });

  it('hides load more after a continued response finishes normally', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          metadata: { finish_reason: 'length', response_cut_off: false },
          parts: [{ type: 'text', text: 'Complete stitched response' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    expect(
      screen.queryByRole('button', { name: 'Load more response' }),
    ).not.toBeInTheDocument();
  });

  it('hides load more after the conversation has moved on', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          metadata: { finish_reason: 'length', response_cut_off: true },
          parts: [{ type: 'text', text: 'Partial response' }],
        },
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Different follow-up' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    expect(screen.getByText('Partial response')).toBeInTheDocument();
    expect(screen.getByText('Different follow-up')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Load more response' }),
    ).not.toBeInTheDocument();
  });

  it('hides the continue button immediately after it is clicked', async () => {
    const sendMessage = jest.fn().mockResolvedValue(undefined);
    const touchSession = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'assistant-message',
          role: 'assistant',
          metadata: { finish_reason: 'length', response_cut_off: true },
          parts: [
            {
              type: 'text',
              text: 'Partial response',
            },
          ],
        },
      ],
      sendMessage,
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });
    mockUseChatSessions.mockReturnValue({
      sessions: [
        {
          thread_id: 'thread-1',
          title: 'Session 1',
          created_at: '2024-01-01T00:00:00+00:00',
          updated_at: '2024-01-01T00:00:00+00:00',
        },
      ],
      loading: false,
      error: null,
      createSession: jest.fn(),
      getSession: jest.fn().mockResolvedValue(null),
      updateSession: jest.fn(),
      deleteSession: jest.fn(),
      touchSession,
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Load more response' }),
      );
    });
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: 'Load more response' }),
      ).not.toBeInTheDocument();
    });
    expect(sendMessage).toHaveBeenCalledWith(undefined, {
      body: {
        continue_message_id: 'assistant-message',
        continue_response: true,
      },
    });
  });

  it('shows an assistant working indicator before assistant text arrives', async () => {
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Run the overview' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'submitted',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat();
    await act(async () => {});

    expect(screen.getByText('Assistant is working...')).toBeInTheDocument();
  });

  it('hides the bypass confirmations toggle without the permission', async () => {
    renderChat();
    await act(async () => {});

    expect(screen.queryByText('Bypass confirmations')).not.toBeInTheDocument();
  });

  it('shows the bypass confirmations toggle, default off, with the permission', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) =>
        permission === 'chat:use' || permission === 'chat:bypass_permissions',
      loading: false,
      currentUser: null,
    });

    renderChat();
    await act(async () => {});

    expect(screen.getByText('Bypass confirmations')).toBeInTheDocument();
    const toggle = screen.getByRole('switch');
    expect(toggle).not.toBeChecked();
  });
  it('reattaches only once the real thread id is known, not to the placeholder', async () => {
    // useChat's resume effect depends on the flag, not on the chat id, so a
    // hardcoded `true` would fire once against the placeholder id and never
    // again — reload recovery would silently do nothing.
    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    const calls = mockUseChat.mock.calls.map(
      ([options]) => options as { id?: string; resume?: boolean },
    );
    const placeholderCalls = calls.filter((c) => c.id === '__pending__');
    const realCalls = calls.filter((c) => c.id === 'thread-1');

    // The placeholder must never resume; the real thread must end up resuming.
    // Early real-thread renders legitimately carry false while history loads —
    // see the hydration test below — so this asserts where it settles, not
    // every intermediate value.
    expect(realCalls.length).toBeGreaterThan(0);
    expect(placeholderCalls.every((c) => c.resume === false)).toBe(true);
    expect(realCalls.at(-1)?.resume).toBe(true);
  });

  it('waits for history before reattaching, so hydration cannot overwrite it', async () => {
    // History is fetched concurrently. Resuming first lets the replay start
    // building the assistant message into an empty chat, and applyHistory then
    // overwrites it once the history it fetched turns out to be longer.
    let releaseHistory: (messages: never[]) => void = () => {};
    mockUseChatHistory.mockReturnValue(
      () =>
        new Promise((resolve) => {
          releaseHistory = resolve as (messages: never[]) => void;
        }),
    );

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    const resumeWhileLoading = mockUseChat.mock.calls
      .map(([options]) => options as { id?: string; resume?: boolean })
      .filter((c) => c.id === 'thread-1');
    expect(resumeWhileLoading.length).toBeGreaterThan(0);
    expect(resumeWhileLoading.every((c) => c.resume === false)).toBe(true);

    await act(async () => {
      releaseHistory([]);
    });

    const after = mockUseChat.mock.calls
      .map(([options]) => options as { id?: string; resume?: boolean })
      .filter((c) => c.id === 'thread-1');
    expect(after.at(-1)?.resume).toBe(true);
  });

  it('tells the server to stop the turn, not just the reader', async () => {
    // The turn runs beside the request now, so closing the stream on its own
    // leaves it generating and able to run the actions it had queued. This one
    // learns the turn by reattaching -- the reloaded-client path, where the id
    // was never handed to this tab at admission.
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        if (String(input).endsWith('/turns/active')) {
          return new Response(
            JSON.stringify({ turn_id: 'turn-42', status: 'existing' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (String(input).endsWith('/stream')) {
          return new Response('data: [DONE]\n\n', { status: 200 });
        }
        return new Response(null, { status: 204 });
      });
    const stop = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        {
          id: 'a1',
          role: 'assistant',
          parts: [{ type: 'text', text: 'Working' }],
        },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop,
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'streaming',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    await activeTransport().reconnectToStream(
      {} as unknown as Parameters<
        SeizuChatTransport<UIMessage>['reconnectToStream']
      >[0],
    );
    fetchMock.mockClear();

    fireEvent.click(screen.getByRole('button', { name: /stop/i }));
    await act(async () => {});

    // Addressed at the turn being watched, not at the thread: this request can
    // be retried, and by then the thread may be running a different turn.
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/chat/turns/turn-42/cancel',
      expect.objectContaining({ method: 'POST', keepalive: true }),
    );
    expect(stop).toHaveBeenCalled();
    fetchMock.mockRestore();
  });

  it.each([
    [409, 'This conversation already has a turn in progress'],
    [404, 'This conversation is no longer available'],
    [503, 'Could not start this turn; please try again'],
  ])('reports a refused admission distinctly (%i)', async (status, message) => {
    // The server distinguishes these and only one is worth retrying, so a
    // single "something went wrong" would tell the user the wrong thing.
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status }));

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    await expect(
      activeTransport().sendMessages(
        sendArgs([
          { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        ]),
      ),
    ).rejects.toThrow(message);
    fetchMock.mockRestore();
  });

  it('can stop a turn as soon as it has been sent', async () => {
    // Stop is enabled from `submitted`, before any frame arrives. Admission is
    // what closes that window: the id comes back from the send itself, so the
    // whole period the button is live is a period it can act on.
    const fetchMock = mockAdmitThenAttach('turn-77');
    const stop = jest.fn();
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop,
      resumeStream: jest.fn(),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'submitted',
      error: undefined,
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    // Drive the send the way the SDK does; nothing has streamed yet.
    await activeTransport().sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );

    fetchMock.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /stop/i }));
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/chat/turns/turn-77/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
    fetchMock.mockRestore();
  });

  it('stops a turn admitted while Stop was already pressed', async () => {
    // The window the previous design could not express: Stop is live from
    // `submitted`, so it can be pressed before admission answers. Aborting the
    // admission does not un-admit it -- the server may already have started the
    // turn -- so the intent has to be held and applied once there is an id.
    let releaseAdmission: (() => void) | null = null;
    const calls: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        calls.push(url);
        if (url.endsWith('/turns')) {
          await new Promise<void>((resolve) => {
            releaseAdmission = resolve;
          });
          return new Response(
            JSON.stringify({ turn_id: 'turn-slow', status: 'created' }),
            { status: 201, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.endsWith('/stream')) {
          return new Response('data: [DONE]\n\n', { status: 200 });
        }
        return new Response(null, { status: 204 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    const transport = activeTransport();

    const sending = transport.sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );
    await act(async () => {});

    // Stop, while the turn still has no id anywhere. Not awaited on the
    // deferred path -- the cancel it asks for cannot happen until admission
    // answers, which this test has not released yet.
    void transport.requestStop();
    expect(calls.some((url) => url.includes('/cancel'))).toBe(false);

    await act(async () => {
      releaseAdmission?.();
      await sending;
    });

    expect(calls).toContain('/api/v1/chat/turns/turn-slow/cancel');
    fetchMock.mockRestore();
  });

  it('retries an ambiguous admission itself, with the same key', async () => {
    // A 503 means the turn may already have been admitted, and the server's
    // repair path is reachable only by asking again with the same key. Waiting
    // for the user to resend does not work: their next message gets a new id,
    // so a new key, which admits nothing and is told the thread is busy.
    const keys: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith('/turns')) {
          keys.push(
            (JSON.parse(String(init?.body)) as { idempotency_key: string })
              .idempotency_key,
          );
          if (keys.length === 1) {
            return new Response('{}', { status: 503 });
          }
          return new Response(
            JSON.stringify({ turn_id: 'turn-repaired', status: 'existing' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    await activeTransport().sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );

    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === '/api/v1/chat/turns/turn-repaired/stream',
      ),
    ).toBe(true);
    fetchMock.mockRestore();
  });

  it('does not retry a decision the server already made', async () => {
    // 409 and 404 are answers, not ambiguity. Repeating them just asks the
    // same question again.
    const attempts: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        attempts.push(String(input));
        return new Response('{}', { status: 409 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    await expect(
      activeTransport().sendMessages(
        sendArgs([
          { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        ]),
      ),
    ).rejects.toThrow('already has a turn in progress');

    expect(attempts).toHaveLength(1);
    fetchMock.mockRestore();
  });

  it('reports a deferred stop that the server refused', async () => {
    // The stop is recorded before the turn has an id, so it is carried out
    // later -- by which point `requestStop` has returned and throwing from the
    // send would be swallowed by the SDK as an expected abort. The warning is
    // the only channel left, and this is the exact race the deferral exists
    // for, so it is the one that must not fail silently.
    let releaseAdmission: (() => void) | null = null;
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/turns')) {
          await new Promise<void>((resolve) => {
            releaseAdmission = resolve;
          });
          return new Response(
            JSON.stringify({ turn_id: 'turn-x', status: 'created' }),
            { status: 201, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.endsWith('/cancel')) return new Response(null, { status: 500 });
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    const transport = activeTransport();

    const sending = transport.sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );
    await act(async () => {});
    await transport.requestStop();

    await act(async () => {
      releaseAdmission?.();
      await sending;
    });

    expect(
      await screen.findByText(/may still be running/i),
    ).toBeInTheDocument();
    fetchMock.mockRestore();
  });

  it("keeps each thread's turn stoppable independently", async () => {
    // The transport outlives any one conversation and the sidebar can switch
    // mid-turn, so its state has to be keyed by thread. Built directly here
    // because the component binds `threadId` to whatever is on screen, and the
    // bug is precisely about a second conversation existing.
    let thread = 'thread-1';
    const stops: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/cancel')) {
          stops.push(url);
          return new Response(null, { status: 204 });
        }
        if (url.endsWith('/turns/active')) {
          // The conversation switched to has nothing running.
          return new Response(null, { status: 204 });
        }
        if (url.includes('/turns') && !url.includes('/stream')) {
          return new Response(
            JSON.stringify({ turn_id: `turn-${thread}`, status: 'created' }),
            { status: 201, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    const transport = standaloneTransport(() => thread);

    await transport.sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );

    // Switch away, and reattach in a conversation that is idle.
    thread = 'thread-2';
    await transport.reconnectToStream(
      {} as unknown as Parameters<
        SeizuChatTransport<UIMessage>['reconnectToStream']
      >[0],
    );
    // Nothing running here, so there is nothing to stop.
    await transport.requestStop();
    expect(stops).toEqual([]);

    // Back to the first, whose turn is still running.
    thread = 'thread-1';
    await transport.requestStop();
    expect(stops).toEqual(['/api/v1/chat/turns/turn-thread-1/cancel']);
    fetchMock.mockRestore();
  });

  it('clears the turn that ended, not whichever thread started last', async () => {
    // The case a single "currently streaming" slot cannot express: A is still
    // streaming when B starts, and A finishes *afterwards*. Anything that
    // records only the latest stream attributes A's completion to B and
    // silently disarms Stop for the turn the user is watching.
    let thread = 'thread-1';
    const stops: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/cancel')) {
          stops.push(url);
          return new Response(null, { status: 204 });
        }
        if (url.includes('/turns') && !url.includes('/stream')) {
          return new Response(
            JSON.stringify({ turn_id: `turn-${thread}`, status: 'created' }),
            { status: 201, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    const transport = standaloneTransport(() => thread);

    await transport.sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );
    // The user switches and starts a second turn while the first still runs.
    thread = 'thread-2';
    await transport.sendMessages(
      sendArgs([
        { id: 'u2', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );

    // Now the *first* one finishes, named by its own turn.
    transport.clearFinishedTurn('turn-thread-1');

    // thread-2's turn is untouched and still stoppable.
    await transport.requestStop();
    expect(stops).toEqual(['/api/v1/chat/turns/turn-thread-2/cancel']);

    // thread-1's is the one that was forgotten.
    thread = 'thread-1';
    await transport.requestStop();
    expect(stops).toEqual(['/api/v1/chat/turns/turn-thread-2/cancel']);
    fetchMock.mockRestore();
  });

  it('clears a finished turn through the id carried in message metadata', async () => {
    // Exercise the component callback, not just the transport helper: the
    // server emits this metadata on the opening frame and the SDK gives the
    // completed message back to onFinish.
    const fetchMock = mockAdmitThenAttach('turn-finished');
    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    const transport = activeTransport();
    await transport.sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );

    const chatOptions = mockUseChat.mock.calls.at(-1)?.[0] as
      | { onFinish?: ChatOnFinishCallback<UIMessage> }
      | undefined;
    await act(async () => {
      chatOptions?.onFinish?.({
        message: {
          id: 'assistant-1',
          role: 'assistant',
          parts: [],
          metadata: { turn_id: 'turn-finished' },
        },
        messages: [],
        isAbort: false,
        isDisconnect: false,
        isError: false,
        finishReason: 'stop',
      });
    });

    fetchMock.mockClear();
    await transport.requestStop();
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });

  it('offers a Retry that replays the unresolved send under its own key', async () => {
    // The repair path is only reachable with the original key and body, and
    // typing the message again mints a new key -- so without an explicit retry
    // the preserved key is unreachable and the turn stays stranded.
    const bodies: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.includes('/turns') && !url.includes('/stream')) {
          bodies.push(String(init?.body));
          if (bodies.length <= 3) return new Response('{}', { status: 503 });
          return new Response(
            JSON.stringify({ turn_id: 'turn-repaired', status: 'existing' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream: jest.fn().mockResolvedValue(undefined),
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: new Error('Could not start this turn; please try again'),
      setMessages: jest.fn(),
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    // A send whose outcome was never established.
    await expect(
      activeTransport().sendMessages(
        sendArgs([
          { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        ]),
      ),
    ).rejects.toThrow();

    // The banner now offers recovery, and taking it replays the same request.
    const retry = await screen.findByRole('button', { name: /retry/i });
    await act(async () => {
      fireEvent.click(retry);
    });

    expect(bodies).toHaveLength(4);
    const keys = bodies.map(
      (b) => (JSON.parse(b) as { idempotency_key: string }).idempotency_key,
    );
    expect(new Set(keys).size).toBe(1);
    // Recovery repeats the same logical request under the same key.
    expect(new Set(bodies).size).toBe(1);
    fetchMock.mockRestore();
  });

  it('keeps Retry visible after switching away from an unresolved thread', async () => {
    // The SDK chat -- including its error -- is recreated per thread, while the
    // transport deliberately retains an unresolved key. Recovery therefore has
    // to render from that retained state rather than from the transient error.
    mockUseChatSessions.mockReturnValue({
      sessions: [
        {
          thread_id: 'thread-1',
          title: 'Session 1',
          created_at: '2024-01-01T00:00:00+00:00',
          updated_at: '2024-01-01T00:00:00+00:00',
        },
        {
          thread_id: 'thread-2',
          title: 'Session 2',
          created_at: '2024-01-02T00:00:00+00:00',
          updated_at: '2024-01-02T00:00:00+00:00',
        },
      ],
      loading: false,
      error: null,
      createSession: jest.fn(),
      getSession: jest.fn().mockResolvedValue(null),
      updateSession: jest.fn(),
      deleteSession: jest.fn(),
      touchSession: jest.fn(),
    });
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 503 }));

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    await expect(
      activeTransport().sendMessages(
        sendArgs([
          { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        ]),
      ),
    ).rejects.toThrow();

    // No SDK error was supplied by the mock, yet unresolved state alone makes
    // the repair reachable.
    expect(
      await screen.findByRole('button', { name: /^retry$/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText('Session 2'));
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /^retry$/i }),
      ).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Session 1'));
    expect(
      await screen.findByRole('button', { name: /^retry$/i }),
    ).toBeInTheDocument();
    fetchMock.mockRestore();
  });

  it('keeps the key when every admission attempt was ambiguous', async () => {
    // A turn may exist server-side after a 503, and its key is the only route
    // back to it. Dropping the key strands that turn until its lease lapses --
    // exactly what the repair path exists to prevent.
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 503 }));

    const unresolved: boolean[] = [];
    const transport = standaloneTransport(
      () => 'thread-1',
      (_threadId, value) => unresolved.push(value),
    );

    await expect(
      transport.sendMessages(
        sendArgs([
          { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        ]),
      ),
    ).rejects.toThrow();

    expect(unresolved.at(-1)).toBe(true);
    // A decision, by contrast, spends the key.
    fetchMock.mockResolvedValue(new Response('{}', { status: 409 }));
    await expect(
      transport.sendMessages(
        sendArgs([
          { id: 'u2', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
        ]),
      ),
    ).rejects.toThrow();
    expect(unresolved.at(-1)).toBe(false);
    fetchMock.mockRestore();
  });

  it('retries an expired admission once under a fresh key', async () => {
    // Expiry is a definitive terminal outcome, not an ambiguous 503: the old
    // key can only resolve to the expired turn, so repairing it forever would
    // never start the user's message. The logical send gets one fresh key.
    const keys: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith('/turns')) {
          keys.push(
            (JSON.parse(String(init?.body)) as { idempotency_key: string })
              .idempotency_key,
          );
          if (keys.length === 1) {
            return new Response('{}', {
              status: 503,
              headers: { 'X-Seizu-Chat-Admission': 'expired' },
            });
          }
          return new Response(
            JSON.stringify({ turn_id: 'turn-fresh', status: 'created' }),
            { status: 201, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    await activeTransport().sendMessages(
      sendArgs([
        { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
      ]),
    );

    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(
      '/api/v1/chat/turns/turn-fresh/stream',
    );
    fetchMock.mockRestore();
  });

  it('gives admission its own deadline so a hung response cannot pin a send', async () => {
    // The SDK's abort signal is deliberately not used for admission, so without
    // a deadline of its own a response that never arrives blocks the retry loop
    // and any stop waiting on a turn id.
    jest.useFakeTimers();
    try {
      const seen: AbortSignal[] = [];
      const fetchMock = jest
        .spyOn(globalThis, 'fetch')
        .mockImplementation(async (_input, init) => {
          if (init?.signal) seen.push(init.signal);
          return new Response('{}', { status: 409 });
        });

      renderChat({ initialPath: '/app/chat/thread-1' });
      await act(async () => {});

      await expect(
        activeTransport().sendMessages(
          sendArgs([
            { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Hi' }] },
          ]),
        ),
      ).rejects.toThrow();

      expect(seen).toHaveLength(1);
      expect(seen[0]).toBeInstanceOf(AbortSignal);
      fetchMock.mockRestore();
    } finally {
      jest.useRealTimers();
    }
  });

  it('reuses one idempotency key across sends of the same message', async () => {
    // The server promises that repeating a request resolves to the turn it
    // already admitted. A fresh key per attempt puts that out of reach: the
    // repeat admits a *second* turn instead of recovering the first.
    const keys: string[] = [];
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith('/turns')) {
          keys.push(
            (JSON.parse(String(init?.body)) as { idempotency_key: string })
              .idempotency_key,
          );
          return new Response('{}', { status: 503 });
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    const transport = activeTransport();
    const message = [
      {
        id: 'u1',
        role: 'user' as const,
        parts: [{ type: 'text' as const, text: 'Hi' }],
      },
    ];

    await expect(transport.sendMessages(sendArgs(message))).rejects.toThrow();
    await expect(transport.sendMessages(sendArgs(message))).rejects.toThrow();

    // Every attempt, internal retries included, carries one key.
    expect(keys.length).toBeGreaterThan(1);
    expect(new Set(keys).size).toBe(1);
    fetchMock.mockRestore();
  });

  it('surfaces a stop the server refused instead of looking successful', async () => {
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/turns/active')) {
          return new Response(
            JSON.stringify({ turn_id: 'turn-42', status: 'existing' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.endsWith('/cancel')) return new Response(null, { status: 403 });
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    const transport = activeTransport();
    await transport.reconnectToStream(
      {} as unknown as Parameters<
        SeizuChatTransport<UIMessage>['reconnectToStream']
      >[0],
    );

    await expect(transport.requestStop()).rejects.toThrow('403');
    fetchMock.mockRestore();
  });

  it('resolves the running turn before reattaching to it', async () => {
    // A reloaded client has a thread but no turn id -- it never saw the
    // admission -- and the SDK's own reconnect would be an unauthenticated GET
    // at a URL that no longer identifies anything.
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        if (String(input).endsWith('/turns/active')) {
          return new Response(
            JSON.stringify({ turn_id: 'turn-99', status: 'existing' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response('data: [DONE]\n\n', { status: 200 });
      });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    fetchMock.mockClear();

    await activeTransport().reconnectToStream(
      {} as unknown as Parameters<
        SeizuChatTransport<UIMessage>['reconnectToStream']
      >[0],
    );

    const [activeUrl, activeInit] = fetchMock.mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(activeUrl).toBe('/api/v1/chat/threads/thread-1/turns/active');
    expect(activeInit.headers).toEqual(
      expect.objectContaining({ Authorization: 'Bearer token-123' }),
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      '/api/v1/chat/turns/turn-99/stream',
    );
    fetchMock.mockRestore();
  });

  it('reports no running turn rather than attaching to nothing', async () => {
    // 204 is how the server says the thread is idle; the SDK maps a null return
    // to "nothing to resume".
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(null, { status: 204 }));

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});
    fetchMock.mockClear();

    const result = await activeTransport().reconnectToStream(
      {} as unknown as Parameters<
        SeizuChatTransport<UIMessage>['reconnectToStream']
      >[0],
    );

    expect(result).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock.mockRestore();
  });

  it('drops the partial reply before resuming after a dropped connection', async () => {
    // The replay starts at the turn's first frame and text-start pushes a fresh
    // part, so keeping the partial message would render the answer twice.
    const setMessages = jest.fn();
    const resumeStream = jest.fn().mockResolvedValue(undefined);
    mockUseChat.mockReturnValue({
      id: 'chat-id',
      messages: [],
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      stop: jest.fn(),
      resumeStream,
      addToolResult: jest.fn(),
      addToolOutput: jest.fn(),
      addToolApprovalResponse: jest.fn(),
      status: 'ready',
      error: undefined,
      setMessages,
      clearError: jest.fn(),
    });

    renderChat({ initialPath: '/app/chat/thread-1' });
    await act(async () => {});

    const chatOptions = mockUseChat.mock.calls.at(-1)?.[0] as
      | { onFinish?: ChatOnFinishCallback<UIMessage> }
      | undefined;
    await act(async () => {
      chatOptions?.onFinish?.({
        message: { id: 'partial', role: 'assistant', parts: [] },
        messages: [],
        isAbort: false,
        isDisconnect: true,
        isError: true,
        finishReason: undefined,
      });
    });

    expect(resumeStream).toHaveBeenCalled();
    const trim = setMessages.mock.calls.at(-1)?.[0] as (
      messages: UIMessage[],
    ) => UIMessage[];
    expect(
      trim([
        { id: 'user-1', role: 'user', parts: [] },
        { id: 'partial', role: 'assistant', parts: [] },
      ]),
    ).toEqual([{ id: 'user-1', role: 'user', parts: [] }]);
    // A turn that dropped before any assistant text arrived has nothing to trim.
    expect(trim([{ id: 'user-1', role: 'user', parts: [] }])).toEqual([
      { id: 'user-1', role: 'user', parts: [] },
    ]);
  });
});
