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
const mockUseChat = useChat as jest.MockedFunction<typeof useChat>;
const mockDefaultChatTransport = DefaultChatTransport as jest.MockedClass<
  typeof DefaultChatTransport
>;
const theme = createTheme();

function renderChat({
  accessToken = 'token-123',
  chatEnabled = true,
  initialPath = '/app/chat',
}: {
  accessToken?: string | null;
  chatEnabled?: boolean;
  initialPath?: string;
} = {}) {
  return render(
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
    </MemoryRouter>,
  );
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

  afterEach(cleanup);

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
      expect(sendMessage).toHaveBeenCalledWith(undefined, {
        body: { resume_confirmation_id: 'confirm-1' },
      });
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
    const originalFetch = globalThis.fetch;
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ confirmations: [] }),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    try {
      renderChat({ initialPath: '/app/chat/thread-1' });

      await waitFor(() => {
        expect(mockUseChat).toHaveBeenCalledWith(
          expect.objectContaining({ id: 'thread-1' }),
        );
      });
      fetchMock.mockClear();

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
        expect(fetchMock).toHaveBeenCalledTimes(1);
      });
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/confirmations?thread_id=thread-1',
        expect.any(Object),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('shows a not-found state for a missing linked session', async () => {
    renderChat({ initialPath: '/app/chat/missing-session' });

    expect(
      await screen.findByText('Chat session not found.'),
    ).toBeInTheDocument();
  });

  it('does not hydrate over existing client messages', async () => {
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
    });
    expect(setMessages).not.toHaveBeenCalledWith(history);
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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: 'Map my graph' });
    });
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

  it('renders streaming Markdown in token batches and leaves the live tail as text', async () => {
    const streamedText = [
      '# Findings',
      '',
      Array.from({ length: 54 }, (_, index) => `word${index}`).join(' '),
    ].join('\n');
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

    expect(
      screen.getByRole('heading', { name: 'Findings', level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText(/word52 word53/)).toBeInTheDocument();
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
});
