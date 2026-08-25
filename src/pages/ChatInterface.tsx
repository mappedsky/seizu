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
import { type ChatOnFinishCallback, type UIMessage } from 'ai';
import { SeizuChatTransport } from 'src/api/chatTransport';
import {
  Alert,
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Card,
  Chip,
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
import PsychologyAlt from '@mui/icons-material/PsychologyAlt';
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
// Matches the API's max_length on the session title (reporting/schema/chat.py).
const MAX_SESSION_TITLE_LENGTH = 200;
const CHAT_HISTORY_POLL_INTERVAL_MS = 2000;
const CHAT_HISTORY_POLL_MAX_ATTEMPTS = 30;
const OUTPUT_LIMIT_NOTICE =
  '\n\n> Response stopped because the model hit its output limit. Ask me to continue from here if you need the rest.';
const OUTPUT_LIMIT_TOOL_NOTICE =
  '\n\nSeizu completed tool work before the cutoff, but the final answer may be incomplete.';

// 'routing' | 'plan' | 'step' | 'verify' are emitted by the chat orchestrator
// (plan->dispatch->verify); 'subagent' wraps a delegated run (e.g. sandbox) whose
// inner tool calls are carried in `children`; the rest come from the single-agent path.
type SeizuChatDetail = {
  kind:
    | 'thinking'
    | 'skill'
    | 'tool'
    | 'routing'
    | 'plan'
    | 'step'
    | 'verify'
    | 'subagent';
  title: string;
  status?: string;
  arguments?: string;
  body?: string;
  step_id?: string;
  // Stable ID emitted alongside the event data so child events can reference
  // this entry as their parent without needing the SSE-level event id.
  detail_id?: string;
  // ID of the parent detail entry; the frontend groups this as a child of that
  // entry. Legacy flat-grouping mechanism, kept for messages persisted before
  // subagent details carried their rows inline in `children`.
  parent_id?: string;
  // Inner rows of a subagent run, rendered nested under this entry.
  children?: SeizuChatDetail[];
};

const KNOWN_DETAIL_KINDS = [
  'thinking',
  'skill',
  'tool',
  'routing',
  'plan',
  'step',
  'verify',
  'subagent',
] as const;

function detailKindIcon(kind: SeizuChatDetail['kind']) {
  const sx = { color: 'text.secondary', fontSize: 14, flexShrink: 0 };
  switch (kind) {
    case 'thinking':
      return <PsychologyAlt sx={sx} />;
    case 'routing':
      return <AltRoute sx={sx} />;
    case 'plan':
      return <Checklist sx={sx} />;
    case 'step':
      return <PlayArrow sx={sx} />;
    case 'verify':
      return <FactCheck sx={sx} />;
    case 'subagent':
      return <SmartToy sx={sx} />;
    default:
      return null;
  }
}

type SeizuChatMessage = UIMessage<
  {
    finish_reason?: string;
    response_cut_off?: boolean;
    seizu_hidden?: boolean;
    // ISO-8601 UTC, stamped when the message was persisted. Absent while a turn
    // is still live (see liveTimestamps) and on messages persisted before
    // timestamps were recorded.
    created_at?: string;
    // Set by useChatHistory on messages read back from the checkpoint.
    seizu_persisted?: boolean;
    // Server-side id of the turn that produced this message, carried on the
    // stream's opening frame. Informational: Stop names the id admission
    // returned, which the client has without waiting for any frame.
    turn_id?: string;
  },
  {
    'seizu-detail': SeizuChatDetail;
  }
>;

const CHAT_LANDING_PATH = '/app/chat';

function chatSessionPath(threadId: string): string {
  return `/app/chat/${encodeURIComponent(threadId)}`;
}

function formatMessageTime(iso: string | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
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

function parseDetail(raw: unknown): SeizuChatDetail | null {
  if (
    typeof raw !== 'object' ||
    raw === null ||
    !('title' in raw) ||
    typeof raw.title !== 'string'
  ) {
    return null;
  }
  const detail = raw as Record<string, unknown>;
  const kind =
    typeof detail.kind === 'string' &&
    (KNOWN_DETAIL_KINDS as readonly string[]).includes(detail.kind)
      ? (detail.kind as SeizuChatDetail['kind'])
      : 'tool';
  const str = (key: string): string | undefined =>
    typeof detail[key] === 'string' ? (detail[key] as string) : undefined;
  const children = Array.isArray(detail.children)
    ? detail.children
        .map(parseDetail)
        .filter((child): child is SeizuChatDetail => child !== null)
    : undefined;
  return {
    kind,
    title: detail.title as string,
    status: str('status'),
    arguments: str('arguments'),
    body: str('body'),
    step_id: str('step_id'),
    detail_id: str('detail_id'),
    parent_id: str('parent_id'),
    children: children && children.length > 0 ? children : undefined,
  };
}

function messageDetails(message: SeizuChatMessage): SeizuChatDetail[] {
  return message.parts
    .map((part): SeizuChatDetail | null => {
      if (!part.type.startsWith('data-') || !('data' in part)) return null;
      return parseDetail(part.data);
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
      detail.step_id === candidate.step_id &&
      detail.detail_id === candidate.detail_id &&
      detail.parent_id === candidate.parent_id &&
      detailsEqual(detail.children ?? [], candidate.children ?? [])
    );
  });
}

type DetailNode = { detail: SeizuChatDetail; children: DetailNode[] };

// Wrap a detail (and its inline children, recursively) as a node. A subagent's
// children carry their own children in turn, so the tree can be arbitrarily deep
// (e.g. step -> subagent -> inner tool calls).
function toDetailNode(detail: SeizuChatDetail): DetailNode {
  return { detail, children: (detail.children ?? []).map(toDetailNode) };
}

// Group the flat detail stream into a hierarchy.  Three mechanisms coexist:
//
// 1. inline children: a subagent detail (e.g. sandbox__delegate) carries its
//    inner rows in `detail.children`; they seed the node's children directly.
//    This is the primary, reload-safe grouping — one detail id holds the whole
//    run, so nothing depends on event ordering or sibling reconciliation.
//
// 2. parent_id grouping (legacy): a detail with parent_id is attached as a child
//    of the detail whose detail_id matches.  Kept so messages persisted before
//    inline children still group on reload.  Pass 1 keys a node map by detail_id;
//    pass 2 assigns these children.
//
// 3. step_id grouping (orchestrator): a `step` detail owns every tool/verify/
//    thinking detail tagged with its step_id, wherever those arrive in the
//    stream.  A subagent grouped here keeps its own inline children, so the
//    orchestrator trace nests two deep.
//
// Ungrouped details (routing, plan, top-level thinking) stay at the root.
function buildDetailTree(details: SeizuChatDetail[]): DetailNode[] {
  // Pass 1: build a node for every detail.  Events with a detail_id are
  // upserted: if an earlier event already created a node for that id, we
  // update its detail in-place rather than creating a second node.  This
  // handles the case where the AI SDK appends both the "running" and
  // "completed" events as separate parts instead of updating the first one.
  const nodeMap = new Map<string, DetailNode>();
  const allNodes: DetailNode[] = [];
  for (const detail of details) {
    if (detail.detail_id && nodeMap.has(detail.detail_id)) {
      // Update the existing node in-place so its position in allNodes is
      // preserved.  Refresh inline children from the newest frame.
      const existing = nodeMap.get(detail.detail_id)!;
      existing.detail = detail;
      if (detail.children && detail.children.length > 0) {
        existing.children = detail.children.map(toDetailNode);
      }
    } else {
      const node = toDetailNode(detail);
      allNodes.push(node);
      if (detail.detail_id) nodeMap.set(detail.detail_id, node);
    }
  }

  // Pass 2: index the step nodes by step_id.  Keyed rather than tracked as "the
  // step we last saw", because steps run in parallel: their tool calls and their
  // thinking interleave in one stream, and a verifier's thinking arrives after
  // every step has opened.  Order therefore says nothing about ownership —
  // step_id does.
  const stepNodes = new Map<string, DetailNode>();
  for (const node of allNodes) {
    if (node.detail.kind === 'step' && node.detail.step_id) {
      stepNodes.set(node.detail.step_id, node);
    }
  }

  // Pass 3: assign each node to either a parent (by parent_id or step_id) or
  // to the root list.  Nodes (not bare details) are attached so a grouped
  // subagent keeps its own children.
  const roots: DetailNode[] = [];

  for (const node of allNodes) {
    const { detail } = node;

    if (detail.parent_id) {
      const parent = nodeMap.get(detail.parent_id);
      if (parent) {
        parent.children.push(node);
        continue;
      }
      // Parent not found (e.g. older message without detail_id) — fall through.
    }

    const step = detail.step_id ? stepNodes.get(detail.step_id) : undefined;
    if (step && step !== node) {
      step.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
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
      icon = <ConstellationSpinner size={15} />;
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
  // Thinking reads as prose and is the reason the pane is open at all, so it
  // starts expanded — a tool call starts closed, because its value is in the
  // one line naming it.  `toggled` is the user's own choice once they make one,
  // so a thought they closed stays closed as the next frame arrives.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const expanded = hasContent && (toggled ?? detail.kind === 'thinking');
  return (
    <Accordion
      disableGutters
      elevation={0}
      square
      expanded={expanded}
      onChange={(_event, next) => setToggled(next)}
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
            <DetailPre
              label={detail.kind === 'thinking' ? 'Thinking' : 'Output'}
              value={detail.body}
            />
          ) : null}
        </AccordionDetails>
      ) : null}
    </Accordion>
  );
}

// Render a detail node and, recursively, its children — indented under the row
// that owns them. Recursion lets the orchestrator trace nest two deep (a step's
// subagent call keeps its own inner tool rows) without special-casing a depth.
function DetailTreeRow({ node }: { node: DetailNode }) {
  return (
    <Box>
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
            <DetailTreeRow
              key={`${child.detail.detail_id ?? child.detail.title}-${childIndex}`}
              node={child}
            />
          ))}
        </Box>
      ) : null}
    </Box>
  );
}

// The turn's trace, at the top of the turn it belongs to and scrolling with it
// like everything else in the conversation. It was briefly sticky, which put it
// over the answer it sat on and meant collapsing and re-expanding itself from a
// measurement of when it had pinned — inferring intent from scroll position, and
// unpredictable to use. A block in normal flow needs none of that.
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
    // Open by default and moved by nothing but a click. Nothing reopens or
    // recloses it as the turn progresses: a block that moves on its own is what
    // made the sticky version unreadable.
    const [expanded, setExpanded] = useState(true);

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
      <Box
        sx={{
          boxSizing: 'border-box',
          maxWidth: { xs: '92%', md: '74%' },
          mb: 0.5,
          width: '100%',
        }}
      >
        <Accordion
          disableGutters
          elevation={0}
          square
          expanded={expanded}
          onChange={(_event, nextExpanded) => setExpanded(nextExpanded)}
          slotProps={{ transition: { timeout: 0 } }}
          sx={{
            bgcolor: 'background.paper',
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            boxSizing: 'border-box',
            mt: 0,
            width: '100%',
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
                // A bound, not a reservation: a two-entry trace takes two rows,
                // and no turn's trace takes over the view it scrolls through.
                maxHeight: 'min(300px, 40vh)',
                overflowY: 'auto',
                px: 1,
                py: 1,
                display: 'flex',
                flexDirection: 'column',
                gap: 0.75,
              }}
            >
              {tree.map((node, index) => (
                <DetailTreeRow
                  key={`${node.detail.step_id ?? node.detail.detail_id ?? node.detail.title}-${index}`}
                  node={node}
                />
              ))}
            </Box>
          </AccordionDetails>
        </Accordion>
      </Box>
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
  const canBypassConfirmations = hasPermission('chat:bypass_permissions');
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
  // The stored id is written, not read: a visit to /app/chat is the landing, not
  // a resumed conversation.
  const { panelOpen, setPanelOpen, setStoredActiveSessionId } =
    useChatLocalStorage();
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [sessionNotFound, setSessionNotFound] = useState(false);
  const [autoTitleError, setAutoTitleError] = useState<string | null>(null);
  // A Stop the server never confirmed. Worth saying out loud: the reader stops
  // either way, so the UI would otherwise look exactly as if it had worked
  // while the turn keeps generating.
  const [stopError, setStopError] = useState<string | null>(null);
  // Every thread whose last send never resolved. A set, not one value: a second
  // ambiguous send in another conversation would otherwise hide the recovery
  // the first one still needs. Mirrors transport state so the banner re-renders.
  const [unresolvedThreads, setUnresolvedThreads] = useState<
    ReadonlySet<string>
  >(() => new Set());
  const [confirmationsOpen, setConfirmationsOpen] = useState(false);
  // Off by default every visit; bypassing confirmations is an explicit,
  // per-session opt-in for users holding chat:bypass_permissions.
  const [bypassConfirmations, setBypassConfirmations] = useState(false);
  const bypassConfirmationsRef = useRef(false);
  const [decidingConfirmationId, setDecidingConfirmationId] = useState<
    string | null
  >(null);
  const [confirmationError, setConfirmationError] = useState<string | null>(
    null,
  );

  // A turn this page admitted itself, waiting for the chat to be keyed to its
  // thread so the SDK can attach to it. Carries the thread id so it attaches to
  // *that* conversation rather than whichever one settles first.
  const [pendingAttach, setPendingAttach] = useState<{
    threadId: string;
    text: string;
  } | null>(null);
  const [creatingSession, setCreatingSession] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // A session created in this tab. It cannot have a turn that predates this
  // page view, so it is the one thread the reattach probe must never run
  // against — see the `resume` flag below.
  const createdHereRef = useRef<string | null>(null);
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
  // resumeStream comes back from useChat, which is declared after the onFinish
  // callback that needs it.
  const resumeStreamRef = useRef<() => Promise<void>>(() => Promise.resolve());
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

    // No thread in the URL: this is the landing, and it stays that way. Neither
    // restoring the last conversation nor minting an empty one — a session is
    // created by asking a question, so the sessions list is a list of questions
    // that were actually asked.
    //
    // Except while an admitted turn is waiting to be attached to.
    // `handleStartSession` sets the thread and navigates together, and the route
    // can land a commit later; clearing the thread in that window unkeys the
    // chat that is about to read the turn.
    if (pendingAttach === null && activeThreadId !== null) {
      setActiveThreadId(null);
      setMessagesRef.current([]);
    }
    setHistoryLoading((current) => (current ? false : current));
    return () => {
      cancelled = true;
    };
  }, [
    routeThreadId,
    activeThreadId,
    pendingAttach,
    sessionsLoading,
    sessionsFeedEnabled,
    sessionsError,
    sessions,
    getSession,
    setStoredActiveSessionId,
  ]);

  // Load history whenever the active session changes.
  useEffect(() => {
    if (!activeThreadId || !sessionsFeedEnabled) return;
    if (createdHereRef.current === activeThreadId) {
      // A session created in this tab, whose first turn this page is about to
      // read from the stream. There is nothing on the server it does not
      // already have, and fetching anyway is what took the answer away:
      // `applyHistory` replaces the whole message list, so a response that
      // arrived between the request and its answer was overwritten -- and
      // gating the attach on the fetch instead made the attach wait on a
      // request with no error path at all.
      setHistoryLoading(false);
      return;
    }
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
      void fetchHistory(activeThreadId)
        .catch(() => {
          // Nothing to hydrate with, but the conversation still has to be
          // usable: a rejected fetch used to leave the page loading forever.
          return [] as SeizuChatMessage[];
        })
        .then((history) => {
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
  bypassConfirmationsRef.current =
    bypassConfirmations && canBypassConfirmations;

  const transport = useMemo(
    () =>
      new SeizuChatTransport<SeizuChatMessage>({
        threadId: () =>
          chatIdRef.current === '__pending__' ? null : chatIdRef.current,
        accessToken: () => accessTokenRef.current,
        onUnresolvedChange: (threadId, unresolved) => {
          // Mirrored into React state: the transport's own copy is a plain
          // object, so without this the recovery banner never renders.
          setUnresolvedThreads((current) => {
            if (current.has(threadId) === unresolved) return current;
            const next = new Set(current);
            if (unresolved) next.add(threadId);
            else next.delete(threadId);
            return next;
          });
        },
        onStopFailed: () => {
          setStopError(
            'Failed to stop this turn on the server; it may still be running.',
          );
        },
        admissionBody: ({ messages, body }) => {
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
            message:
              resumeConfirmationId || continueResponse
                ? ''
                : latestUserText(messages),
            ...(resumeConfirmationId
              ? { resume_confirmation_id: resumeConfirmationId }
              : {}),
            ...(continueResponse ? { continue_response: true } : {}),
            ...(continueMessageId
              ? { continue_message_id: continueMessageId }
              : {}),
            ...(bypassConfirmationsRef.current
              ? { bypass_confirmations: true }
              : {}),
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
    ({ message, isDisconnect }) => {
      // The session is no longer newborn: it has a finished turn, so returning
      // to it later has something to reattach to and the probe is welcome again.
      // Held only for the first turn, which is the one it would have raced.
      createdHereRef.current = null;
      if (message.role === 'assistant') {
        setPendingContinuationTargetMessageId((current) =>
          current === message.id ? null : current,
        );
      }
      if (isDisconnect) {
        // The turn is still running on the server; only our connection to it
        // died. Reconnecting replays the turn from its first frame, so the
        // partial assistant message has to go first: the SDK resumes *into*
        // the last assistant message and text-start pushes a fresh part, so
        // keeping it would show the answer twice.
        setMessagesRef.current((current) => {
          const last = current.at(-1);
          return last?.role === 'assistant' ? current.slice(0, -1) : current;
        });
        void resumeStreamRef.current();
        return;
      }
      // The turn is over, so it is no longer what Stop should reach: leaving it
      // pending means a Stop pressed during the *next* send -- before that one
      // is admitted -- cancels this finished turn and silently does nothing to
      // the live one.
      //
      // The transport decides *which* thread finished, because by now the user
      // may have switched conversations and `activeThreadId` is the wrong
      // answer -- clearing on that basis disarms Stop for the turn they are
      // actually watching. It also keeps a send whose outcome is unknown: that
      // turn may exist server-side and only its key can reach it.
      transport.clearFinishedTurn(message.metadata?.turn_id);
      if (!activeThreadId) return;
      window.setTimeout(() => {
        void fetchConfirmations();
      }, 0);
    },
    [activeThreadId, fetchConfirmations, transport],
  );

  // Seeds the `Chat` that `useChat` builds when the id changes — the only
  // moment it reads this. The landing's question has to be in the transcript
  // *before* the reattach fires, and a `setMessages` from an effect is too
  // late: the chat for the new thread is built during render, so anything
  // written to the old one is gone, and anything written after the attach has
  // started overwrites the assistant message it has already pushed.
  const initialMessages = useMemo(
    () =>
      pendingAttach
        ? [
            {
              id: `msg_${crypto.randomUUID()}`,
              role: 'user' as const,
              parts: [{ type: 'text' as const, text: pendingAttach.text }],
            },
          ]
        : undefined,
    [pendingAttach],
  );

  const {
    messages,
    sendMessage,
    setMessages,
    status,
    stop,
    error,
    clearError,
    resumeStream,
  } = useChat<SeizuChatMessage>({
    id: chatId,
    messages: initialMessages,
    experimental_throttle: CHAT_MESSAGE_THROTTLE_MS,
    onFinish: handleChatFinish,
    transport,
    // Reattach to a turn that outlived the last page view.
    //
    // Gated on the real thread id rather than hardcoded true: useChat's resume
    // effect depends on this flag, not on the chat id, so passing true up front
    // would fire it once against the placeholder id and never again once the
    // real one arrived — reload recovery would silently do nothing.
    //
    // Gated on hydration too, because history is fetched concurrently. Resuming
    // first lets the replay start building the assistant message into an empty
    // chat, and applyHistory then overwrites it when the history it fetched
    // turns out to be longer. Waiting means the messages present when resume
    // fires are the persisted ones, which only ever contain finished turns —
    // so there is never a partial message to resume into.
    //
    // A session started from the landing gets here the same way a reload does:
    // its turn is already admitted, so the transport knows the id and
    // `reconnectToStream` attaches to it. Calling `resumeStream` by hand instead
    // did not show the turn at all, and the reload path is the one known to
    // work — so there is one route into it, not two.
    resume: activeThreadId !== null && !historyLoading,
  });

  messagesRef.current = messages;
  setMessagesRef.current = setMessages;
  resumeStreamRef.current = resumeStream;

  const [retrying, setRetrying] = useState(false);
  const handleRetryUnresolved = useCallback(() => {
    // Replays the original request, key and body intact, and hands the stream
    // back to the SDK exactly as a fresh send would.
    setRetrying(true);
    void transport
      .retryUnresolved()
      .then(() => {
        clearError();
        void resumeStreamRef.current();
      })
      .catch(() => {
        // Left as it was: still unresolved, still retryable.
      })
      .finally(() => setRetrying(false));
  }, [clearError, transport]);

  const handleStop = useCallback(() => {
    // Closing the stream is not enough: the turn is produced elsewhere, so
    // without being told it keeps generating and can still run the actions it
    // had queued.
    //
    // The transport owns this, because it is the only thing that knows whether
    // the turn has an id yet. Stop is live from `submitted`, before admission
    // answers, and in that window there is nothing here to name — recording the
    // intent lets it be applied the moment there is.
    setStopError(null);
    void transport.requestStop().catch(() => {
      // The reader stops regardless, so a lost request leaves the user looking
      // at a stopped response while the turn runs on. `keepalive` covers the
      // common cause (navigating away in the same gesture); beyond that there
      // is nothing useful to do from here, so say so rather than retrying.
      setStopError(
        'Failed to stop this turn on the server; it may still be running.',
      );
    });
    stop();
  }, [stop, transport]);

  const busy = status === 'submitted' || status === 'streaming';
  const visibleMessages = useMemo(
    () => messages.filter((message) => message.metadata?.seizu_hidden !== true),
    [messages],
  );
  // A message only carries a server timestamp once it comes back from
  // /chat/history; the copy the stream produces has none. So a message that is
  // neither timed nor yet persisted is stamped with the browser's clock, and
  // that stands in until the thread is reloaded. Server metadata always wins.
  //
  // A message the server has persisted but not timed is a turn from before
  // timestamps were recorded: it gets no time at all rather than today's date,
  // which is what stamping every untimed message did to every old conversation.
  const [liveTimestamps, setLiveTimestamps] = useState<Record<string, string>>(
    {},
  );
  useEffect(() => {
    const now = new Date().toISOString();
    setLiveTimestamps((current) => {
      let added = false;
      const next: Record<string, string> = {};
      for (const message of messages) {
        if (message.metadata?.created_at || message.metadata?.seizu_persisted)
          continue;
        const stamp = current[message.id];
        next[message.id] = stamp ?? now;
        added ||= stamp === undefined;
      }
      // Rebuilt from the current conversation, so switching threads or
      // hydrating history also drops the stamps those messages no longer need.
      // Identity is preserved when nothing moved, or this would re-run forever.
      const unchanged =
        !added && Object.keys(next).length === Object.keys(current).length;
      return unchanged ? current : next;
    });
  }, [messages]);
  const messageTime = useCallback(
    (message: SeizuChatMessage): string =>
      formatMessageTime(
        message.metadata?.created_at ?? liveTimestamps[message.id],
      ),
    [liveTimestamps],
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
    // Store the full opening message (up to the API's limit) rather than a
    // 40-character preview: the sidebar truncates visually with CSS, so a
    // pre-truncated title left the hover tooltip showing the ellipsis too.
    const title =
      firstUserMessageText.length > MAX_SESSION_TITLE_LENGTH
        ? `${firstUserMessageText.slice(0, MAX_SESSION_TITLE_LENGTH - 1).trimEnd()}…`
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

  // No API call: "new session" is the landing, and the session is created by the
  // question. It does not clear `activeThreadId` either — the URL-sync effect
  // owns that, and a thread cleared here while the route still named it made
  // that effect re-adopt the session, refetch its history and show it again on
  // the way out (spinner, old conversation, then the landing).
  const handleNewSession = useCallback(() => {
    setMessages([]);
    setSessionNotFound(false);
    setAutoTitleError(null);
    setPendingAttach(null);
    navigate(CHAT_LANDING_PATH);
  }, [navigate, setMessages]);

  // The landing's question: create the session, then let the send happen once
  // `useChat` has re-keyed to it. Sending in this callback would post to the
  // chat instance still keyed to the previous thread.
  const handleStartSession = useCallback(
    async (text: string) => {
      setStartError(null);
      setCreatingSession(true);
      try {
        const session = await createSession();
        // Admitted before anything is navigated or re-keyed, so the question is
        // the server's before the UI has to be right about anything. If this
        // throws, the turn does not exist and the user still has their text.
        await transport.startTurn(session.thread_id, text);
        setMessages([]);
        setHistoryLoading(false);
        setSessionNotFound(false);
        setAutoTitleError(null);
        createdHereRef.current = session.thread_id;
        setActiveThreadId(session.thread_id);
        setStoredActiveSessionId(session.thread_id);
        setPendingAttach({ threadId: session.thread_id, text });
        navigate(chatSessionPath(session.thread_id));
      } catch {
        setStartError('Could not start a new conversation. Please try again.');
      } finally {
        setCreatingSession(false);
      }
    },
    [createSession, navigate, setMessages, setStoredActiveSessionId, transport],
  );

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
        setActiveThreadId(null);
        setMessages([]);
        setHistoryLoading(false);
        setAutoTitleError(null);
        navigate(CHAT_LANDING_PATH, { replace: true });
      }
    },
    [
      activeThreadId,
      sessions,
      deleteSession,
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

  useEffect(() => {
    // The chat for this thread has been built and seeded with the question, and
    // `resume` has taken it from there. All that is left is to stop holding it:
    // the URL-sync effect reads this to know a thread is mid-handover.
    if (!pendingAttach || activeThreadId !== pendingAttach.threadId) return;
    setPendingAttach(null);
    touchSession(activeThreadId);
  }, [pendingAttach, activeThreadId, touchSession]);

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

  const handleCopyMessage = async (message: SeizuChatMessage) => {
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
        <ConstellationSpinner size={48} />
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
          onNewSession={handleNewSession}
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

  // No conversation open: ask for one. Nothing is created until the question is
  // asked, so an abandoned visit leaves no empty session behind. Keyed on the
  // route alone: `activeThreadId` trails it by a commit on the way in and out,
  // and reading both here is what showed the conversation being left.
  if (!routeThreadId) {
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
          onNewSession={handleNewSession}
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
            flexDirection: 'column',
            justifyContent: 'center',
            minHeight: 0,
            minWidth: 0,
            overflow: 'auto',
          }}
        >
          <Box sx={{ maxWidth: 720, width: '100%' }}>
            <Typography
              component="h1"
              sx={{ mb: 2, textAlign: 'center' }}
              variant="h2"
            >
              What should we ask the security graph?
            </Typography>
            {startError ? (
              <Alert severity="error" sx={{ mb: 1 }}>
                {startError}
              </Alert>
            ) : null}
            <ChatInput
              // Disabled rather than busy while the session is being created:
              // busy turns the send button into a Stop, and there is nothing
              // here to stop.
              busy={false}
              disabled={creatingSession}
              onSubmit={(text) => void handleStartSession(text)}
              onStop={() => {}}
            />
          </Box>
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
        onNewSession={handleNewSession}
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
                    const timestamp = messageTime(message);
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
                            // The user's turn keeps its time and copy action out
                            // of the way until the message is pointed at (or
                            // reached with the keyboard). Pointer-less devices
                            // never fire hover, so there they stay visible.
                            '&:hover .seizu-user-message-actions, &:focus-within .seizu-user-message-actions':
                              { opacity: 1 },
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
                            <ChatMessageDetails
                              details={details}
                              isStreaming={message.id === streamingMessageId}
                            />
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
                                          void handleCopyMessage(message);
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
                                  {timestamp ? (
                                    <Typography
                                      variant="caption"
                                      sx={{ color: 'text.secondary' }}
                                    >
                                      {timestamp}
                                    </Typography>
                                  ) : null}
                                </Box>
                              </Box>
                            )}
                          </Box>
                          {message.role === 'user' ? (
                            <Box
                              aria-label="User message actions"
                              className="seizu-user-message-actions"
                              sx={{
                                alignItems: 'center',
                                display: 'flex',
                                gap: 0.5,
                                mt: 0.25,
                                opacity: 0,
                                transition: 'opacity 120ms ease',
                                '@media (hover: none)': { opacity: 1 },
                              }}
                            >
                              {timestamp ? (
                                <Typography
                                  variant="caption"
                                  sx={{ color: 'text.secondary' }}
                                >
                                  {timestamp}
                                </Typography>
                              ) : null}
                              <Tooltip
                                title={copied ? 'Copied' : 'Copy message'}
                              >
                                <span>
                                  <IconButton
                                    aria-label="Copy your message"
                                    disabled={!text}
                                    onClick={() => {
                                      void handleCopyMessage(message);
                                    }}
                                    size="small"
                                    sx={{ color: 'text.secondary', p: 0.25 }}
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
                          ) : null}
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

        {startError ? (
          // A first question that never reached its session. Shown here as well
          // as on the landing, because by the time it fails the user is already
          // looking at the conversation it was meant to start.
          <Alert
            onClose={() => setStartError(null)}
            severity="error"
            sx={{ flexShrink: 0, my: 0.5 }}
          >
            {startError}
          </Alert>
        ) : null}
        {(activeThreadId && unresolvedThreads.has(activeThreadId)) || error ? (
          <Alert
            severity="error"
            sx={{ flexShrink: 0, my: 0.5 }}
            action={
              // Only offered when the outcome was never established. The turn
              // may be running server-side, and replaying the original request
              // under its original key is the one thing that can resolve to it
              // -- typing the message again mints a new key and admits a
              // second turn, or is told the thread is busy.
              activeThreadId && unresolvedThreads.has(activeThreadId) ? (
                <Button
                  color="inherit"
                  size="small"
                  disabled={retrying}
                  onClick={handleRetryUnresolved}
                >
                  {retrying ? 'Retrying…' : 'Retry'}
                </Button>
              ) : null
            }
          >
            {activeThreadId && unresolvedThreads.has(activeThreadId)
              ? 'We could not confirm your message was received. It may still be running.'
              : error?.message}
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

        {stopError ? (
          <Alert
            severity="warning"
            onClose={() => setStopError(null)}
            sx={{ flexShrink: 0, my: 0.5 }}
          >
            {stopError}
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
          bypassConfirmations={bypassConfirmations}
          disabled={disabled}
          onBypassConfirmationsChange={setBypassConfirmations}
          onStop={handleStop}
          onSubmit={handleSubmit}
          showBypassConfirmations={canBypassConfirmations}
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
