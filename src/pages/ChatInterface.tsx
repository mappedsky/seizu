import {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useChat } from '@ai-sdk/react';
import {
  DefaultChatTransport,
  type ChatOnFinishCallback,
  type UIMessage,
} from 'ai';
import {
  Alert,
  Box,
  Card,
  CircularProgress,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import SmartToy from '@mui/icons-material/SmartToy';
import Person from '@mui/icons-material/Person';
import Check from '@mui/icons-material/Check';
import ContentCopy from '@mui/icons-material/ContentCopy';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import { usePermissionState } from 'src/hooks/usePermissions';
import { useChatHistory } from 'src/hooks/useChatHistory';
import { useChatLocalStorage } from 'src/hooks/useChatLocalStorage';
import { useChatSessions } from 'src/hooks/useChatSessions';
import {
  type ActionConfirmation,
  useConfirmationsApi,
} from 'src/hooks/useConfirmationsApi';
import { useFeature } from 'src/features.context';
import { MarkdocRenderer } from 'src/components/markdoc/renderer';
import ChatInput from 'src/components/ChatInput';
import ChatSessionsPanel from 'src/components/ChatSessionsPanel';
import ChatConfirmationsPanel from 'src/components/ChatConfirmationsPanel';
import { pageContentSx } from 'src/theme/layout';

const CHAT_MESSAGE_THROTTLE_MS = 50;

function chatSessionPath(threadId: string): string {
  return `/app/chat/${encodeURIComponent(threadId)}`;
}

function messageText(message: UIMessage): string {
  return message.parts
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('');
}

function latestUserText(messages: UIMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === 'user') return messageText(message);
  }
  return '';
}

export default function ChatInterface() {
  const navigate = useNavigate();
  const { threadId: routeThreadId } = useParams<{ threadId?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { accessToken } = useContext(AuthContext);
  const { auth_required } = useContext(AuthConfigContext);
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const chatEnabled = useFeature('chat');
  const fetchHistory = useChatHistory();

  const canUseChat = hasPermission('chat:use');
  const waitingForToken = auth_required && !accessToken;
  const sessionsFeedEnabled =
    chatEnabled && !permissionsLoading && !waitingForToken && canUseChat;

  const {
    sessions,
    loading: sessionsLoading,
    error: sessionsError,
    createSession,
    getSession,
    updateSession,
    deleteSession,
    touchSession,
  } = useChatSessions(sessionsFeedEnabled);

  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const {
    getStoredActiveSessionId,
    panelOpen,
    setPanelOpen,
    setStoredActiveSessionId,
  } = useChatLocalStorage();
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [sessionNotFound, setSessionNotFound] = useState(false);
  const [autoTitleError, setAutoTitleError] = useState<string | null>(null);
  const [confirmationsOpen, setConfirmationsOpen] = useState(false);
  const [decidingConfirmationId, setDecidingConfirmationId] = useState<
    string | null
  >(null);
  const [confirmationError, setConfirmationError] = useState<string | null>(
    null,
  );

  const creatingInitialSessionRef = useRef(false);
  const autoTitleAttemptRef = useRef<string | null>(null);
  const messagesRef = useRef<UIMessage[]>([]);
  const setMessagesRef = useRef<
    (messages: UIMessage[] | ((messages: UIMessage[]) => UIMessage[])) => void
  >(() => {});
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const accessTokenRef = useRef(accessToken);
  const chatIdRef = useRef('__pending__');
  const resumeConfirmationIdRef = useRef<string | null>(null);
  const consumedResumeParamRef = useRef<string | null>(null);

  // Keep the selected session in sync with the URL.
  useEffect(() => {
    if (sessionsLoading || !sessionsFeedEnabled) return;
    if (sessionsError) return;
    let cancelled = false;
    setSessionNotFound((current) => (current ? false : current));

    if (routeThreadId) {
      const knownSession = sessions.find((s) => s.thread_id === routeThreadId);
      if (knownSession) {
        if (activeThreadId !== knownSession.thread_id) {
          setMessagesRef.current([]);
          setHistoryLoading(true);
          setActiveThreadId(knownSession.thread_id);
          setStoredActiveSessionId(knownSession.thread_id);
        }
      } else {
        void getSession(routeThreadId)
          .then((session) => {
            if (cancelled) return;
            if (session) {
              if (activeThreadId !== session.thread_id) {
                setMessagesRef.current([]);
                setHistoryLoading(true);
                setActiveThreadId(session.thread_id);
                setStoredActiveSessionId(session.thread_id);
              }
            } else {
              if (activeThreadId !== null) {
                setActiveThreadId(null);
                setMessagesRef.current([]);
              }
              setHistoryLoading((current) => (current ? false : current));
              setSessionNotFound(true);
            }
          })
          .catch(() => {
            if (cancelled) return;
            if (activeThreadId !== null) {
              setActiveThreadId(null);
              setMessagesRef.current([]);
            }
            setHistoryLoading((current) => (current ? false : current));
            setSessionNotFound(true);
          });
      }
      return () => {
        cancelled = true;
      };
    }

    const storedId = getStoredActiveSessionId();
    const target =
      sessions.find((s) => s.thread_id === storedId) ?? sessions[0];
    if (target) {
      navigate(chatSessionPath(target.thread_id), { replace: true });
      return () => {
        cancelled = true;
      };
    }

    if (activeThreadId) {
      navigate(chatSessionPath(activeThreadId), { replace: true });
      return () => {
        cancelled = true;
      };
    }

    if (!creatingInitialSessionRef.current) {
      creatingInitialSessionRef.current = true;
      void createSession()
        .then((session) => {
          if (cancelled) return;
          setActiveThreadId(session.thread_id);
          setStoredActiveSessionId(session.thread_id);
          navigate(chatSessionPath(session.thread_id), { replace: true });
        })
        .catch(() => {
          if (!cancelled) setHistoryLoading(false);
        })
        .finally(() => {
          creatingInitialSessionRef.current = false;
        });
    }
    return () => {
      cancelled = true;
      creatingInitialSessionRef.current = false;
    };
  }, [
    routeThreadId,
    activeThreadId,
    sessionsLoading,
    sessionsFeedEnabled,
    sessionsError,
    sessions,
    createSession,
    getSession,
    getStoredActiveSessionId,
    navigate,
    setStoredActiveSessionId,
  ]);

  // Load history whenever the active session changes.
  useEffect(() => {
    if (!activeThreadId || !sessionsFeedEnabled) return;
    let cancelled = false;
    setHistoryLoading(true);
    void fetchHistory(activeThreadId).then((history) => {
      if (cancelled) return;
      if (messagesRef.current.length === 0) {
        setMessagesRef.current(history);
      }
      setHistoryLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [activeThreadId, sessionsFeedEnabled, fetchHistory]);

  // chatId used as the useChat key; never null so hooks stay unconditional.
  const chatId = activeThreadId ?? '__pending__';
  accessTokenRef.current = accessToken;
  chatIdRef.current = chatId;

  const transport = useMemo(
    () =>
      new DefaultChatTransport<UIMessage>({
        api: '/api/v1/chat/stream',
        headers: { 'X-Seizu-Csrf': '1' },
        prepareSendMessagesRequest: ({ messages, headers, body }) => {
          const currentToken = accessTokenRef.current;
          const resumeConfirmationId =
            typeof body?.resume_confirmation_id === 'string'
              ? body.resume_confirmation_id
              : resumeConfirmationIdRef.current;
          resumeConfirmationIdRef.current = null;
          return {
            headers: {
              ...headers,
              ...(currentToken
                ? { Authorization: `Bearer ${currentToken}` }
                : {}),
            },
            body: {
              message: resumeConfirmationId ? '' : latestUserText(messages),
              thread_id: chatIdRef.current,
              ...(resumeConfirmationId
                ? { resume_confirmation_id: resumeConfirmationId }
                : {}),
            },
          };
        },
      }),
    [],
  );

  const {
    confirmations,
    loading: confirmationsLoading,
    error: confirmationsError,
    fetchConfirmations,
    decideConfirmation,
  } = useConfirmationsApi(activeThreadId);

  const handleChatFinish = useCallback<ChatOnFinishCallback<UIMessage>>(() => {
    if (!activeThreadId) return;
    window.setTimeout(() => {
      void fetchConfirmations();
    }, 0);
  }, [activeThreadId, fetchConfirmations]);

  const { messages, sendMessage, setMessages, status, stop, error } =
    useChat<UIMessage>({
      id: chatId,
      experimental_throttle: CHAT_MESSAGE_THROTTLE_MS,
      onFinish: handleChatFinish,
      transport,
    });

  messagesRef.current = messages;
  setMessagesRef.current = setMessages;

  const busy = status === 'submitted' || status === 'streaming';

  // Auto-title: update session title from first user message when title is empty.
  const activeSession = useMemo(
    () => sessions.find((s) => s.thread_id === activeThreadId),
    [activeThreadId, sessions],
  );
  const firstUserMessageText = useMemo(() => {
    const firstUserMessage = messages.find((m) => m.role === 'user');
    return firstUserMessage ? messageText(firstUserMessage).trim() : '';
  }, [messages]);
  useEffect(() => {
    if (!activeSession || activeSession.title || !activeThreadId) return;
    if (autoTitleAttemptRef.current === activeThreadId) return;
    if (!firstUserMessageText) return;
    const title =
      firstUserMessageText.length > 40
        ? `${firstUserMessageText.slice(0, 40).trimEnd()}…`
        : firstUserMessageText;
    autoTitleAttemptRef.current = activeThreadId;
    setAutoTitleError(null);
    void updateSession(activeThreadId, title).catch(() => {
      autoTitleAttemptRef.current = null;
      setAutoTitleError('Failed to name this session automatically.');
    });
  }, [firstUserMessageText, activeSession, activeThreadId, updateSession]);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  const handleSelectSession = useCallback(
    (threadId: string) => {
      if (threadId === activeThreadId) return;
      setActiveThreadId(threadId);
      setMessages([]);
      setHistoryLoading(true);
      setSessionNotFound(false);
      setAutoTitleError(null);
      setStoredActiveSessionId(threadId);
      navigate(chatSessionPath(threadId));
    },
    [activeThreadId, navigate, setMessages, setStoredActiveSessionId],
  );

  const handleNewSession = useCallback(async () => {
    const session = await createSession();
    setActiveThreadId(session.thread_id);
    setMessages([]);
    setHistoryLoading(false);
    setSessionNotFound(false);
    setAutoTitleError(null);
    setStoredActiveSessionId(session.thread_id);
    navigate(chatSessionPath(session.thread_id));
  }, [createSession, navigate, setMessages, setStoredActiveSessionId]);

  const handleDeleteSession = useCallback(
    async (threadId: string) => {
      await deleteSession(threadId);
      if (activeThreadId !== threadId) return;
      // Active session was deleted — switch to the next available or create a new one.
      const remaining = sessions.filter((s) => s.thread_id !== threadId);
      if (remaining.length > 0) {
        const next = remaining[0];
        setActiveThreadId(next.thread_id);
        setMessages([]);
        setHistoryLoading(true);
        setAutoTitleError(null);
        setStoredActiveSessionId(next.thread_id);
        navigate(chatSessionPath(next.thread_id), { replace: true });
      } else {
        const newSession = await createSession();
        setActiveThreadId(newSession.thread_id);
        setMessages([]);
        setHistoryLoading(false);
        setAutoTitleError(null);
        setStoredActiveSessionId(newSession.thread_id);
        navigate(chatSessionPath(newSession.thread_id), { replace: true });
      }
    },
    [
      activeThreadId,
      sessions,
      deleteSession,
      createSession,
      navigate,
      setMessages,
      setStoredActiveSessionId,
    ],
  );

  const handleSubmit = useCallback(
    (text: string) => {
      if (!activeThreadId) return;
      touchSession(activeThreadId);
      void sendMessage({ text });
    },
    [activeThreadId, touchSession, sendMessage],
  );

  const handleConfirmationDecision = useCallback(
    async (
      confirmation: ActionConfirmation,
      decision: 'approved' | 'denied',
    ) => {
      const pendingCount = confirmations.filter(
        (c) => c.status === 'pending',
      ).length;
      const wasLastPending = pendingCount === 1;
      setDecidingConfirmationId(confirmation.confirmation_id);
      setConfirmationError(null);
      try {
        await decideConfirmation(confirmation.confirmation_id, decision);
        await fetchConfirmations();
        if (decision === 'approved' && activeThreadId && wasLastPending) {
          resumeConfirmationIdRef.current = confirmation.confirmation_id;
          touchSession(activeThreadId);
          await Promise.resolve(
            sendMessage(undefined, {
              body: { resume_confirmation_id: confirmation.confirmation_id },
            }),
          );
        }
      } catch {
        setConfirmationError('Failed to approve or resume this confirmation.');
      } finally {
        setDecidingConfirmationId(null);
      }
    },
    [
      activeThreadId,
      confirmations,
      decideConfirmation,
      fetchConfirmations,
      sendMessage,
      touchSession,
    ],
  );

  useEffect(() => {
    if (!activeThreadId || busy) return;
    const resumeConfirmationId = searchParams.get('resume_confirmation_id');
    if (!resumeConfirmationId) return;
    if (consumedResumeParamRef.current === resumeConfirmationId) return;
    consumedResumeParamRef.current = resumeConfirmationId;
    resumeConfirmationIdRef.current = resumeConfirmationId;
    touchSession(activeThreadId);
    try {
      void Promise.resolve(
        sendMessage(undefined, {
          body: { resume_confirmation_id: resumeConfirmationId },
        }),
      ).catch(() => {
        setConfirmationError('Failed to resume the approved confirmation.');
      });
    } catch {
      setConfirmationError('Failed to resume the approved confirmation.');
    }
    const next = new URLSearchParams(searchParams);
    next.delete('resume_confirmation_id');
    setSearchParams(next, { replace: true });
  }, [
    activeThreadId,
    busy,
    searchParams,
    sendMessage,
    setSearchParams,
    touchSession,
  ]);

  const handleCopyAssistantResponse = async (message: UIMessage) => {
    const text = messageText(message);
    if (!text || !navigator.clipboard) return;
    await navigator.clipboard.writeText(text);
    setCopiedMessageId(message.id);
    window.setTimeout(() => {
      setCopiedMessageId((current) =>
        current === message.id ? null : current,
      );
    }, 1800);
  };

  if (!chatEnabled) {
    return (
      <Box sx={pageContentSx}>
        <Typography>Chat is not enabled.</Typography>
      </Box>
    );
  }

  if (permissionsLoading || waitingForToken) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!canUseChat) {
    return (
      <Box sx={pageContentSx}>
        <Typography>You do not have access to chat.</Typography>
      </Box>
    );
  }

  const disabled = !activeThreadId;

  if (sessionsError) {
    return (
      <Box sx={pageContentSx}>
        <Alert severity="error">{sessionsError}</Alert>
      </Box>
    );
  }

  if (sessionNotFound) {
    return (
      <Box
        sx={{
          display: 'flex',
          height: 'calc(100vh - 64px)',
          overflow: 'hidden',
        }}
      >
        <ChatSessionsPanel
          open={panelOpen}
          onToggle={() => setPanelOpen((v) => !v)}
          sessions={sessions}
          loading={sessionsLoading}
          activeThreadId={activeThreadId}
          onSelectSession={handleSelectSession}
          onNewSession={() => void handleNewSession()}
          onDeleteSession={handleDeleteSession}
          onRenameSession={updateSession}
        />
        <Box
          sx={{
            ...pageContentSx,
            alignItems: 'center',
            boxSizing: 'border-box',
            display: 'flex',
            flex: 1,
            justifyContent: 'center',
            minWidth: 0,
          }}
        >
          <Alert severity="warning">Chat session not found.</Alert>
        </Box>
      </Box>
    );
  }

  return (
    <Box
      sx={{ display: 'flex', height: 'calc(100vh - 64px)', overflow: 'hidden' }}
    >
      <ChatSessionsPanel
        open={panelOpen}
        onToggle={() => setPanelOpen((v) => !v)}
        sessions={sessions}
        loading={sessionsLoading}
        activeThreadId={activeThreadId}
        onSelectSession={handleSelectSession}
        onNewSession={() => void handleNewSession()}
        onDeleteSession={handleDeleteSession}
        onRenameSession={updateSession}
      />

      {/* Main chat area */}
      <Box
        sx={{
          display: 'flex',
          flex: 1,
          flexDirection: 'column',
          ...pageContentSx,
          boxSizing: 'border-box',
          minHeight: 0,
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        <Box
          sx={{
            boxSizing: 'border-box',
            flex: 1,
            minHeight: 0,
            overflow: 'hidden',
          }}
        >
          <Card
            sx={{
              display: 'flex',
              height: '100%',
              minHeight: 0,
            }}
          >
            <Box
              sx={{
                flex: 1,
                minHeight: 0,
                overflowY: 'auto',
                px: { xs: 1.5, md: 2 },
                py: 1.5,
              }}
            >
              {sessionsLoading || (historyLoading && messages.length === 0) ? (
                <Box
                  sx={{
                    alignItems: 'center',
                    display: 'flex',
                    height: '100%',
                    justifyContent: 'center',
                  }}
                >
                  <CircularProgress />
                </Box>
              ) : messages.length === 0 ? (
                <Box
                  sx={{
                    alignItems: 'center',
                    color: 'text.secondary',
                    display: 'flex',
                    height: '100%',
                    justifyContent: 'center',
                    textAlign: 'center',
                  }}
                >
                  <Typography variant="body2">
                    Start a conversation with the graph assistant.
                  </Typography>
                </Box>
              ) : (
                <>
                  {messages.map((message) => {
                    const text = messageText(message);
                    const copied = copiedMessageId === message.id;
                    return (
                      <Box
                        key={message.id}
                        sx={{
                          alignItems:
                            message.role === 'user' ? 'flex-end' : 'flex-start',
                          display: 'flex',
                          flexDirection: 'column',
                          mb: 1.5,
                        }}
                      >
                        <Box
                          sx={{
                            alignItems: 'center',
                            color: 'text.secondary',
                            display: 'flex',
                            gap: 0.75,
                            mb: 0.5,
                          }}
                        >
                          {message.role === 'user' ? (
                            <Person fontSize="small" />
                          ) : (
                            <SmartToy fontSize="small" />
                          )}
                          <Typography variant="caption">
                            {message.role === 'user' ? 'You' : 'Assistant'}
                          </Typography>
                        </Box>
                        <Box
                          sx={{
                            bgcolor:
                              message.role === 'user'
                                ? 'primary.main'
                                : 'action.hover',
                            borderRadius: 2,
                            color:
                              message.role === 'user'
                                ? 'primary.contrastText'
                                : 'text.primary',
                            maxWidth: { xs: '92%', md: '74%' },
                            px: 1.5,
                            py: 1,
                            whiteSpace:
                              message.role === 'user' ? 'pre-wrap' : 'normal',
                            wordBreak: 'break-word',
                          }}
                        >
                          {message.role === 'user' ? (
                            <Typography variant="body2">
                              {text || (busy ? '...' : '')}
                            </Typography>
                          ) : (
                            <Box
                              sx={(theme) => ({
                                fontSize: theme.typography.body2.fontSize,
                                lineHeight: theme.typography.body2.lineHeight,
                                '& > :first-child': { mt: 0 },
                                '& > :last-child': { mb: 0 },
                                '& p': {
                                  fontSize: 'inherit',
                                  lineHeight: 'inherit',
                                  mb: 1,
                                  mt: 0,
                                },
                                '& ul, & ol': {
                                  fontSize: 'inherit',
                                  lineHeight: 'inherit',
                                  my: 1,
                                  pl: 2.5,
                                },
                                '& li': { mb: 0.5, pl: 0.25 },
                                '& li > p': { mb: 0.5 },
                                '& h2, & h3, & h4, & h5, & h6': {
                                  fontSize: theme.typography.subtitle2.fontSize,
                                  fontWeight: 600,
                                  lineHeight:
                                    theme.typography.subtitle2.lineHeight,
                                  mb: 1,
                                  mt: 1.25,
                                },
                                '& hr': {
                                  border: 0,
                                  borderTop: 1,
                                  borderColor: 'divider',
                                  my: 2,
                                },
                                '& pre': {
                                  bgcolor: 'background.paper',
                                  border: 1,
                                  borderColor: 'divider',
                                  borderRadius: 1,
                                  fontFamily: '"JetBrains Mono", monospace',
                                  fontSize: theme.typography.caption.fontSize,
                                  lineHeight: 1.55,
                                  my: 1.25,
                                  overflowX: 'auto',
                                  p: 1,
                                  whiteSpace: 'pre',
                                },
                                '& code': {
                                  bgcolor: 'background.paper',
                                  borderRadius: 0.5,
                                  fontFamily: '"JetBrains Mono", monospace',
                                  fontSize: '0.9em',
                                  px: 0.5,
                                },
                                '& pre code': {
                                  bgcolor: 'transparent',
                                  borderRadius: 0,
                                  display: 'block',
                                  fontSize: 'inherit',
                                  lineHeight: 'inherit',
                                  p: 0,
                                  whiteSpace: 'inherit',
                                },
                                '& img': {
                                  height: 'auto',
                                  maxWidth: '100%',
                                },
                              })}
                            >
                              <MarkdocRenderer
                                source={text || (busy ? '...' : '')}
                                untrustedUrls
                              />
                              <Box
                                aria-label="Assistant response actions"
                                sx={{
                                  alignItems: 'center',
                                  display: 'flex',
                                  gap: 0.5,
                                  justifyContent: 'flex-start',
                                  mt: 1,
                                }}
                              >
                                <Tooltip
                                  title={copied ? 'Copied' : 'Copy response'}
                                >
                                  <span>
                                    <IconButton
                                      aria-label="Copy assistant response"
                                      disabled={!text}
                                      onClick={() => {
                                        void handleCopyAssistantResponse(
                                          message,
                                        );
                                      }}
                                      size="small"
                                      sx={{
                                        color: 'text.secondary',
                                        p: 0.25,
                                      }}
                                    >
                                      {copied ? (
                                        <Check sx={{ fontSize: 16 }} />
                                      ) : (
                                        <ContentCopy sx={{ fontSize: 16 }} />
                                      )}
                                    </IconButton>
                                  </span>
                                </Tooltip>
                              </Box>
                            </Box>
                          )}
                        </Box>
                      </Box>
                    );
                  })}
                  {busy ? (
                    <Box
                      sx={{
                        alignItems: 'center',
                        color: 'text.secondary',
                        display: 'flex',
                        gap: 1,
                        mb: 1.5,
                      }}
                    >
                      <CircularProgress size={14} />
                      <Typography variant="body2">
                        Assistant is working...
                      </Typography>
                    </Box>
                  ) : null}
                </>
              )}
              <div ref={scrollRef} />
            </Box>
          </Card>
        </Box>

        {error ? (
          <Alert severity="error" sx={{ flexShrink: 0, my: 0.5 }}>
            {error.message}
          </Alert>
        ) : null}

        {autoTitleError ? (
          <Alert
            severity="warning"
            onClose={() => setAutoTitleError(null)}
            sx={{ flexShrink: 0, my: 0.5 }}
          >
            {autoTitleError}
          </Alert>
        ) : null}

        {confirmationError ? (
          <Alert
            severity="error"
            onClose={() => setConfirmationError(null)}
            sx={{ flexShrink: 0, my: 0.5 }}
          >
            {confirmationError}
          </Alert>
        ) : null}

        <ChatInput
          busy={busy}
          disabled={disabled}
          onSubmit={handleSubmit}
          onStop={stop}
        />
      </Box>
      <ChatConfirmationsPanel
        confirmations={confirmations}
        loading={confirmationsLoading}
        error={confirmationsError}
        open={confirmationsOpen}
        decidingId={decidingConfirmationId}
        onToggle={() => setConfirmationsOpen((v) => !v)}
        onDecision={(confirmation, decision) => {
          void handleConfirmationDecision(confirmation, decision);
        }}
      />
    </Box>
  );
}
