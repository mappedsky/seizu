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
import {
  DefaultChatTransport,
  type ChatOnFinishCallback,
  type UIMessage,
} from 'ai';

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

jest.mock('ai', () => ({
  DefaultChatTransport: jest.fn().mockImplementation((options: object) => ({
    options,
  })),
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
const mockDefaultChatTransport = DefaultChatTransport as jest.MockedClass<
  typeof DefaultChatTransport
>;
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

    const transportOptions = mockDefaultChatTransport.mock.calls.at(-1)?.[0];
    expect(transportOptions).toBeDefined();
    if (!transportOptions) throw new Error('missing transport options');
    expect(transportOptions.api).toBe('/api/v1/chat/stream');
    expect(transportOptions.headers).toEqual({
      'X-Seizu-Csrf': '1',
    });

    const prepared = transportOptions.prepareSendMessagesRequest?.({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Hello graph' }],
        },
      ],
      requestMetadata: undefined,
      body: undefined,
      credentials: 'same-origin',
      headers: transportOptions.headers as HeadersInit,
      api: '/api/v1/chat/stream',
      trigger: 'submit-message',
      messageId: 'user-message',
    }) as
      | {
          headers: Record<string, string>;
          body: { message: string; thread_id: string };
        }
      | undefined;

    expect(prepared?.headers).toEqual({
      Authorization: 'Bearer token-123',
      'X-Seizu-Csrf': '1',
    });
    expect(prepared?.body.message).toBe('Hello graph');
    expect(prepared?.body.thread_id).toBe(threadId);
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

    const transportOptions = mockDefaultChatTransport.mock.calls.at(-1)?.[0];
    if (!transportOptions) throw new Error('missing transport options');
    const prepared = transportOptions.prepareSendMessagesRequest?.({
      id: 'chat-id',
      messages: [
        {
          id: 'user-message',
          role: 'user',
          parts: [{ type: 'text', text: 'Fresh token please' }],
        },
      ],
      requestMetadata: undefined,
      body: undefined,
      credentials: 'same-origin',
      headers: transportOptions.headers as HeadersInit,
      api: '/api/v1/chat/stream',
      trigger: 'submit-message',
      messageId: 'user-message',
    }) as { headers: Record<string, string> } | undefined;

    expect(prepared?.headers.Authorization).toBe('Bearer token-2');
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

  it('collapses streaming details when the response completes', async () => {
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

    mockUseChat.mockReturnValue(chatResult('ready'));
    rerender(chatTree());

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Details 1' })).toHaveAttribute(
        'aria-expanded',
        'false',
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Details 1' }));
    expect(screen.getByRole('button', { name: 'Details 1' })).toHaveAttribute(
      'aria-expanded',
      'true',
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
});
