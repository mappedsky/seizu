import {
  useCallback,
  useContext,
  useEffect,
  memo,
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
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Card,
  Chip,
  CircularProgress,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import ExpandMore from '@mui/icons-material/ExpandMore';
import KeyboardDoubleArrowDown from '@mui/icons-material/KeyboardDoubleArrowDown';
import CheckCircle from '@mui/icons-material/CheckCircle';
import HourglassEmpty from '@mui/icons-material/HourglassEmpty';
import ErrorOutlined from '@mui/icons-material/ErrorOutlined';
import Psychology from '@mui/icons-material/Psychology';
import AltRoute from '@mui/icons-material/AltRoute';
import Checklist from '@mui/icons-material/Checklist';
import PlayArrow from '@mui/icons-material/PlayArrow';
import FactCheck from '@mui/icons-material/FactCheck';
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
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { pageContentSx } from 'src/theme/layout';

const CHAT_MESSAGE_THROTTLE_MS = 50;
const CHAT_HISTORY_POLL_INTERVAL_MS = 2000;
const CHAT_HISTORY_POLL_MAX_ATTEMPTS = 30;
const OUTPUT_LIMIT_NOTICE =
  '\n\n> Response stopped because the model hit its output limit. Ask me to continue from here if you need the rest.';
const OUTPUT_LIMIT_TOOL_NOTICE =
  '\n\nSeizu completed tool work before the cutoff, but the final answer may be incomplete.';

// 'routing' | 'plan' | 'step' | 'verify' are emitted by the chat orchestrator
// (plan->dispatch->verify); the rest come from the single-agent path.
type SeizuChatDetail = {
  kind: 'thinking' | 'skill' | 'tool' | 'routing' | 'plan' | 'step' | 'verify';
  title: string;
  status?: string;
  arguments?: string;
  body?: string;
  step_id?: string;
};

const KNOWN_DETAIL_KINDS = [
  'thinking',
  'skill',
  'tool',
  'routing',
  'plan',
  'step',
  'verify',
] as const;

function detailKindIcon(kind: SeizuChatDetail['kind']) {
  const sx = { color: 'text.secondary', fontSize: 14, flexShrink: 0 };
  switch (kind) {
    case 'routing':
      return <AltRoute sx={sx} />;
    case 'plan':
      return <Checklist sx={sx} />;
    case 'step':
      return <PlayArrow sx={sx} />;
    case 'verify':
      return <FactCheck sx={sx} />;
    default:
      return null;
  }
}

type SeizuChatMessage = UIMessage<
  {
    finish_reason?: string;
    response_cut_off?: boolean;
    seizu_hidden?: boolean;
  },
  {
    'seizu-detail': SeizuChatDetail;
  }
>;

function chatSessionPath(threadId: string): string {
  return `/app/chat/${encodeURIComponent(threadId)}`;
}

function messageText(message: SeizuChatMessage): string {
  return message.parts
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('');
}

function latestUserText(messages: SeizuChatMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === 'user') return messageText(message);
  }
  return '';
}

function shouldPollChatHistory(messages: SeizuChatMessage[]): boolean {
  const lastMessage = messages.at(-1);
  return lastMessage?.role === 'user';
}

function messageDetails(message: SeizuChatMessage): SeizuChatDetail[] {
  return message.parts
    .map((part): SeizuChatDetail | null => {
      if (!part.type.startsWith('data-') || !('data' in part)) return null;
      const detail = part.data;
      if (
        typeof detail !== 'object' ||
        detail === null ||
        !('title' in detail) ||
        typeof detail.title !== 'string'
      ) {
        return null;
      }
      const kind =
        'kind' in detail &&
        typeof detail.kind === 'string' &&
        (KNOWN_DETAIL_KINDS as readonly string[]).includes(detail.kind)
          ? (detail.kind as SeizuChatDetail['kind'])
          : 'tool';
      return {
        kind,
        title: detail.title,
        status:
          'status' in detail && typeof detail.status === 'string'
            ? detail.status
            : undefined,
        arguments:
          'arguments' in detail && typeof detail.arguments === 'string'
            ? detail.arguments
            : undefined,
        body:
          'body' in detail && typeof detail.body === 'string'
            ? detail.body
            : undefined,
        step_id:
          'step_id' in detail && typeof detail.step_id === 'string'
            ? detail.step_id
            : undefined,
      };
    })
    .filter((detail): detail is SeizuChatDetail => detail !== null);
}

function canLoadMore(message: SeizuChatMessage): boolean {
  if (messageText(message).includes('{% continuation /%}')) return false;
  return (
    message.role === 'assistant' &&
    (message.metadata?.response_cut_off === true ||
      (message.metadata?.finish_reason === 'length' &&
        message.metadata.response_cut_off !== false))
  );
}

function stripOutputLimitNotice(text: string): string {
  return text
    .replace(OUTPUT_LIMIT_NOTICE, '')
    .replace(OUTPUT_LIMIT_TOOL_NOTICE, '')
    .trimEnd();
}

function hiddenResumeMessage(confirmationId: string): SeizuChatMessage {
  return {
    id: `resume-${confirmationId}`,
    role: 'user',
    metadata: { seizu_hidden: true },
    parts: [],
  };
}

// Split a streaming response into completed blocks (separated by blank lines)
// and the still-growing final block. Each completed block has a fixed source, so
// rendering them through their own <MarkdocRenderer> lets Markdoc parse each one
// exactly once and memoize it — only new text is ever parsed, old blocks never
// re-parse or flicker. An open fenced code block keeps accumulating into the tail
// (its blank lines must not split it) until the closing fence arrives.
function splitIntoBlocks(text: string): { blocks: string[]; tail: string } {
  const merged: string[] = [];
  let buffer = '';
  for (const segment of text.split('\n\n')) {
    buffer = buffer === '' ? segment : `${buffer}\n\n${segment}`;
    const fenceOpen = ((buffer.match(/```/g)?.length ?? 0) & 1) === 1;
    if (!fenceOpen) {
      merged.push(buffer);
      buffer = '';
    }
  }
  if (buffer !== '') merged.push(buffer);
  // The last entry is still in progress (no trailing blank line / open fence);
  // everything before it is settled and safe to parse once.
  return { blocks: merged.slice(0, -1), tail: merged.at(-1) ?? '' };
}

// Live-rendered assistant message: settled blocks as memoized Markdown, plus the
// in-progress block as plain text. Parsing Markdoc on the whole growing response
// every token is O(n^2) and freezes the tab; here only newly-settled blocks are
// ever parsed. Pure (no state/timer), so it can neither loop nor flicker old
// text. The parent renders completed messages through Markdoc directly, so this
// only ever handles the single in-flight message.
function StreamingMarkdown({ text }: { text: string }) {
  const { blocks, tail } = useMemo(() => splitIntoBlocks(text), [text]);
  const tailText = tail.replace(/^\n+/, '');
  // Settled blocks are append-only, so their identity is pinned by index; the
  // memo keeps their element subtrees stable across tokens (re-created only when
  // a new block settles). blocks.length is the stable key for that set.
  const renderedBlocks = useMemo(
    () =>
      blocks.map((block, index) => (
        <MarkdocRenderer key={index} source={block} untrustedUrls />
      )),

    [blocks.length],
  );
  return (
    <>
      {renderedBlocks}
      {tailText || blocks.length === 0 ? (
        <Typography
          component="div"
          sx={{
            fontSize: 'inherit',
            lineHeight: 'inherit',
            mt: blocks.length > 0 ? 1 : 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {tailText || '...'}
        </Typography>
      ) : null}
    </>
  );
}

function detailsEqual(
  previous: readonly SeizuChatDetail[],
  next: readonly SeizuChatDetail[],
): boolean {
  if (previous.length !== next.length) return false;
  return previous.every((detail, index) => {
    const candidate = next[index];
    return (
      detail.kind === candidate.kind &&
      detail.title === candidate.title &&
      detail.status === candidate.status &&
      detail.arguments === candidate.arguments &&
      detail.body === candidate.body &&
      detail.step_id === candidate.step_id
    );
  });
}

type DetailNode = { detail: SeizuChatDetail; children: SeizuChatDetail[] };

// Group the flat detail stream into a step hierarchy: a `step` detail opens a
// group, and the tool/verify details tagged with its step_id nest under it.
// Ungrouped details (routing, plan, top-level thinking) stay at the root.
function buildDetailTree(details: SeizuChatDetail[]): DetailNode[] {
  const nodes: DetailNode[] = [];
  let currentStep: DetailNode | null = null;
  for (const detail of details) {
    if (detail.kind === 'step') {
      currentStep = { detail, children: [] };
      nodes.push(currentStep);
    } else if (
      detail.step_id &&
      currentStep &&
      currentStep.detail.step_id === detail.step_id
    ) {
      currentStep.children.push(detail);
    } else {
      nodes.push({ detail, children: [] });
    }
  }
  return nodes;
}

// Status icons replace text labels: a checkmark/spinner/hourglass/error reads at
// a glance, and the raw status stays available on hover. "blocked" is a failure
// (a verification that did not pass, a permission/error block); a genuine wait is
// the distinct "awaiting"/"pending" confirmation state.
function DetailStatus({ status }: { status?: string }) {
  if (!status) return null;
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  let icon;
  switch (status) {
    case 'completed':
      icon = <CheckCircle sx={{ fontSize: 15, color: 'success.main' }} />;
      break;
    case 'running':
      icon = <CircularProgress size={12} thickness={6} />;
      break;
    case 'awaiting':
    case 'pending':
      icon = <HourglassEmpty sx={{ fontSize: 15, color: 'warning.main' }} />;
      break;
    case 'blocked':
    case 'failed':
    case 'denied':
      icon = <ErrorOutlined sx={{ fontSize: 15, color: 'error.main' }} />;
      break;
    default:
      icon = <CheckCircle sx={{ fontSize: 15, color: 'text.disabled' }} />;
  }
  return (
    <Tooltip title={label}>
      <Box
        component="span"
        sx={{
          alignItems: 'center',
          display: 'inline-flex',
          flexShrink: 0,
          ml: 'auto',
        }}
      >
        {icon}
      </Box>
    </Tooltip>
  );
}

function DetailRow({ detail }: { detail: SeizuChatDetail }) {
  const hasContent = Boolean(detail.arguments || detail.body);
  return (
    <Accordion
      disableGutters
      elevation={0}
      square
      slotProps={{ transition: { timeout: 0, unmountOnExit: true } }}
      sx={{
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        '&:before': { display: 'none' },
      }}
    >
      <AccordionSummary
        expandIcon={hasContent ? <ExpandMore fontSize="small" /> : null}
        sx={{
          minHeight: 30,
          px: 1,
          py: 0,
          cursor: hasContent ? 'pointer' : 'default',
          '& .MuiAccordionSummary-content': {
            alignItems: 'center',
            gap: 0.75,
            my: 0.5,
          },
        }}
      >
        {detailKindIcon(detail.kind)}
        <Typography
          sx={{ fontWeight: 600, minWidth: 0, wordBreak: 'break-word' }}
          variant="caption"
        >
          {detail.title}
        </Typography>
        <DetailStatus status={detail.status} />
      </AccordionSummary>
      {hasContent ? (
        <AccordionDetails sx={{ px: 1, pt: 0, pb: 1 }}>
          {detail.arguments ? (
            <DetailPre label="Arguments" value={detail.arguments} />
          ) : null}
          {detail.body ? (
            <DetailPre label="Output" value={detail.body} />
          ) : null}
        </AccordionDetails>
      ) : null}
    </Accordion>
  );
}

const ChatMessageDetails = memo(
  function ChatMessageDetails({
    details,
    isStreaming,
  }: {
    details: SeizuChatDetail[];
    isStreaming?: boolean;
  }) {
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const tree = useMemo(() => buildDetailTree(details), [details]);

    // Follow the content while it streams, but only when the user is already near
    // the bottom — never yank them away from something they scrolled up to read.
    useEffect(() => {
      const el = scrollRef.current;
      if (!el || !isStreaming) return;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      if (nearBottom) el.scrollTop = el.scrollHeight;
    }, [details, isStreaming]);

    if (details.length === 0) return null;
    return (
      <Accordion
        disableGutters
        elevation={0}
        square
        defaultExpanded={isStreaming}
        slotProps={{ transition: { timeout: 0 } }}
        sx={{
          bgcolor: 'background.paper',
          border: 1,
          borderColor: 'divider',
          borderRadius: 1,
          mb: 1,
          mt: 0,
          width: '100%',
          boxSizing: 'border-box',
          zIndex: 1,
          borderTopLeftRadius: 0,
          borderTopRightRadius: 0,
          '&:before': { display: 'none' },
        }}
      >
        <AccordionSummary
          expandIcon={<ExpandMore fontSize="small" />}
          sx={{
            minHeight: 32,
            px: 1,
            py: 0,
            '& .MuiAccordionSummary-content': {
              alignItems: 'center',
              gap: 0.75,
              my: 0.5,
            },
          }}
        >
          <Psychology sx={{ color: 'text.secondary', fontSize: 16 }} />
          <Typography color="text.secondary" variant="caption">
            Details
          </Typography>
          <Chip
            label={details.length}
            size="small"
            sx={{ height: 18, minWidth: 18 }}
          />
        </AccordionSummary>
        <AccordionDetails sx={{ px: 0, py: 0 }}>
          <Box
            ref={scrollRef}
            sx={{
              height: 300,
              overflowY: 'auto',
              px: 1,
              py: 1,
              display: 'flex',
              flexDirection: 'column',
              gap: 0.75,
            }}
          >
            {tree.map((node, index) => (
              <Box key={`${node.detail.step_id ?? node.detail.title}-${index}`}>
                <DetailRow detail={node.detail} />
                {node.children.length > 0 ? (
                  <Box
                    sx={{
                      ml: 1,
                      mt: 0.5,
                      pl: 1,
                      borderLeft: 2,
                      borderColor: 'divider',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 0.5,
                    }}
                  >
                    {node.children.map((child, childIndex) => (
                      <DetailRow
                        key={`${child.title}-${childIndex}`}
                        detail={child}
                      />
                    ))}
                  </Box>
                ) : null}
              </Box>
            ))}
          </Box>
        </AccordionDetails>
      </Accordion>
    );
  },
  (previous, next) =>
    previous.isStreaming === next.isStreaming &&
    detailsEqual(previous.details, next.details),
);

function DetailPre({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ mt: 0.75 }}>
      <Typography color="text.secondary" variant="caption">
        {label}
      </Typography>
      <Box
        component="pre"
        sx={{
          bgcolor: 'action.hover',
          border: 1,
          borderColor: 'divider',
          borderRadius: 1,
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 11,
          lineHeight: 1.45,
          m: 0,
          mt: 0.25,
          overflowX: 'auto',
          p: 0.75,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {value}
      </Box>
    </Box>
  );
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
  const [historyPolling, setHistoryPolling] = useState(false);
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
  const messagesRef = useRef<SeizuChatMessage[]>([]);
  const setMessagesRef = useRef<
    (
      messages:
        | SeizuChatMessage[]
        | ((messages: SeizuChatMessage[]) => SeizuChatMessage[]),
    ) => void
  >(() => {});
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const accessTokenRef = useRef(accessToken);
  const chatIdRef = useRef('__pending__');
  const resumeConfirmationIdRef = useRef<string | null>(null);
  const consumedResumeParamRef = useRef<string | null>(null);
  const [
    pendingContinuationTargetMessageId,
    setPendingContinuationTargetMessageId,
  ] = useState<string | null>(null);

  useEffect(() => {
    setPendingContinuationTargetMessageId(null);
  }, [activeThreadId]);

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
    let pollTimer: number | undefined;
    let pollAttempts = 0;
    setHistoryPolling(false);

    const applyHistory = (history: SeizuChatMessage[]) => {
      const currentMessages = messagesRef.current;
      const currentLatest = currentMessages.at(-1);
      if (
        currentMessages.length === 0 ||
        history.length > currentMessages.length ||
        (currentLatest?.role === 'user' &&
          history.length >= currentMessages.length)
      ) {
        setMessagesRef.current(history);
      }
    };

    const loadHistory = () => {
      void fetchHistory(activeThreadId).then((history) => {
        if (cancelled) return;
        applyHistory(history);
        setHistoryLoading(false);
        if (
          shouldPollChatHistory(history) &&
          pollAttempts < CHAT_HISTORY_POLL_MAX_ATTEMPTS
        ) {
          pollAttempts += 1;
          setHistoryPolling(true);
          pollTimer = window.setTimeout(
            loadHistory,
            CHAT_HISTORY_POLL_INTERVAL_MS,
          );
        } else {
          setHistoryPolling(false);
        }
      });
    };

    setHistoryLoading(true);
    loadHistory();
    return () => {
      cancelled = true;
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
    };
  }, [activeThreadId, sessionsFeedEnabled, fetchHistory]);

  // chatId used as the useChat key; never null so hooks stay unconditional.
  const chatId = activeThreadId ?? '__pending__';
  accessTokenRef.current = accessToken;
  chatIdRef.current = chatId;

  const transport = useMemo(
    () =>
      new DefaultChatTransport<SeizuChatMessage>({
        api: '/api/v1/chat/stream',
        headers: { 'X-Seizu-Csrf': '1' },
        prepareSendMessagesRequest: ({ messages, headers, body }) => {
          const currentToken = accessTokenRef.current;
          const resumeConfirmationId =
            typeof body?.resume_confirmation_id === 'string'
              ? body.resume_confirmation_id
              : resumeConfirmationIdRef.current;
          const continueResponse = body?.continue_response === true;
          const continueMessageId =
            typeof body?.continue_message_id === 'string'
              ? body.continue_message_id
              : undefined;
          resumeConfirmationIdRef.current = null;
          return {
            headers: {
              ...headers,
              ...(currentToken
                ? { Authorization: `Bearer ${currentToken}` }
                : {}),
            },
            body: {
              message:
                resumeConfirmationId || continueResponse
                  ? ''
                  : latestUserText(messages),
              thread_id: chatIdRef.current,
              ...(resumeConfirmationId
                ? { resume_confirmation_id: resumeConfirmationId }
                : {}),
              ...(continueResponse ? { continue_response: true } : {}),
              ...(continueMessageId
                ? { continue_message_id: continueMessageId }
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

  const handleChatFinish = useCallback<ChatOnFinishCallback<SeizuChatMessage>>(
    ({ message }) => {
      if (message.role === 'assistant') {
        setPendingContinuationTargetMessageId((current) =>
          current === message.id ? null : current,
        );
      }
      if (!activeThreadId) return;
      window.setTimeout(() => {
        void fetchConfirmations();
      }, 0);
    },
    [activeThreadId, fetchConfirmations],
  );

  const { messages, sendMessage, setMessages, status, stop, error } =
    useChat<SeizuChatMessage>({
      id: chatId,
      experimental_throttle: CHAT_MESSAGE_THROTTLE_MS,
      onFinish: handleChatFinish,
      transport,
    });

  messagesRef.current = messages;
  setMessagesRef.current = setMessages;

  const busy = status === 'submitted' || status === 'streaming';
  const visibleMessages = useMemo(
    () => messages.filter((message) => message.metadata?.seizu_hidden !== true),
    [messages],
  );
  // Id of the assistant message currently being streamed. It renders through
  // <StreamingMarkdown> (plain text while tokens arrive, parsed on quiesce)
  // rather than feeding the whole growing response to Markdoc every token.
  const streamingMessageId =
    status === 'streaming' ? (messages.at(-1)?.id ?? null) : null;
  const continuableMessage = useMemo(() => {
    const lastMessage = messages.at(-1);
    return lastMessage && canLoadMore(lastMessage) ? lastMessage : null;
  }, [messages]);

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
  }, [historyPolling, messages]);

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
            sendMessage(hiddenResumeMessage(confirmation.confirmation_id), {
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
        sendMessage(hiddenResumeMessage(resumeConfirmationId), {
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

  const handleCopyAssistantResponse = async (message: SeizuChatMessage) => {
    const text = messageText(message);
    if (!text || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    setCopiedMessageId(message.id);
    window.setTimeout(() => {
      setCopiedMessageId((current) =>
        current === message.id ? null : current,
      );
    }, 1800);
  };

  const handleLoadMore = useCallback(
    (message: SeizuChatMessage) => {
      if (!activeThreadId || busy) return;
      setPendingContinuationTargetMessageId(message.id);
      touchSession(activeThreadId);
      void Promise.resolve(
        sendMessage(undefined, {
          body: {
            continue_message_id: message.id,
            continue_response: true,
          },
        }),
      ).catch(() => {
        setPendingContinuationTargetMessageId((current) =>
          current === message.id ? null : current,
        );
      });
    },
    [activeThreadId, busy, sendMessage, touchSession],
  );

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
              {sessionsLoading ||
              (historyLoading && visibleMessages.length === 0) ? (
                <Box
                  sx={{
                    alignItems: 'center',
                    display: 'flex',
                    height: '100%',
                    justifyContent: 'center',
                  }}
                >
                  <ConstellationSpinner size={64} />
                </Box>
              ) : visibleMessages.length === 0 ? (
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
                  {visibleMessages.map((message) => {
                    const text = messageText(message);
                    const details = messageDetails(message);
                    const copied = copiedMessageId === message.id;
                    const loadMore = continuableMessage?.id === message.id;
                    const isContinuationSource =
                      pendingContinuationTargetMessageId === message.id;
                    return (
                      <Box key={message.id}>
                        <Box
                          sx={{
                            alignItems:
                              message.role === 'user'
                                ? 'flex-end'
                                : 'flex-start',
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
                          {message.role === 'assistant' &&
                          details.length > 0 ? (
                            <Box
                              sx={{
                                boxSizing: 'border-box',
                                maxWidth: { xs: '92%', md: '74%' },
                                mb: 0.5,
                                position: 'sticky',
                                top: (theme) => theme.spacing(-1.5),
                                width: '100%',
                                zIndex: 2,
                              }}
                            >
                              <ChatMessageDetails
                                details={details}
                                isStreaming={message.id === streamingMessageId}
                              />
                            </Box>
                          ) : null}
                          <Box
                            sx={{
                              bgcolor:
                                message.role === 'user'
                                  ? 'primary.main'
                                  : 'action.hover',
                              border: message.role === 'user' ? 0 : 1,
                              borderColor:
                                message.role === 'user'
                                  ? 'transparent'
                                  : 'divider',
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
                                  color: 'text.primary',
                                  fontSize: theme.typography.body2.fontSize,
                                  lineHeight: theme.typography.body2.lineHeight,
                                  width: '100%',
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
                                    fontSize:
                                      theme.typography.subtitle2.fontSize,
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
                                {message.id === streamingMessageId ? (
                                  <StreamingMarkdown
                                    text={stripOutputLimitNotice(text)}
                                  />
                                ) : (
                                  <MarkdocRenderer
                                    source={
                                      stripOutputLimitNotice(text) ||
                                      (busy ? '...' : '')
                                    }
                                    untrustedUrls
                                  />
                                )}
                                {loadMore && !isContinuationSource ? (
                                  <Box sx={{ mt: 1 }}>
                                    <Button
                                      aria-label="Load more response"
                                      disabled={busy}
                                      fullWidth
                                      onClick={() => {
                                        handleLoadMore(message);
                                      }}
                                      startIcon={<KeyboardDoubleArrowDown />}
                                      sx={{
                                        justifyContent: 'center',
                                      }}
                                      variant="outlined"
                                    >
                                      Continue response
                                    </Button>
                                  </Box>
                                ) : null}
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
                      <ConstellationSpinner size={28} />
                      <Typography variant="body2">
                        Assistant is working...
                      </Typography>
                    </Box>
                  ) : historyPolling ? (
                    <Box
                      sx={{
                        alignItems: 'center',
                        color: 'text.secondary',
                        display: 'flex',
                        gap: 1,
                        mb: 1.5,
                      }}
                    >
                      <ConstellationSpinner size={28} />
                      <Typography variant="body2">
                        Waiting for the response...
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
