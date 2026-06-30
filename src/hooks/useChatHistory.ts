import { useCallback } from 'react';
import type { UIMessage } from 'ai';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

// One persisted detail row. `children` carries a subagent's nested rows (e.g. the
// sandbox inner-tool calls); detail_id/parent_id are the legacy flat-grouping ids
// kept for messages persisted before subagent rows were nested inline.
interface HistoryDetail {
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
  route?: string;
  detail_id?: string;
  parent_id?: string;
  children?: HistoryDetail[];
}

type SeizuChatHistoryMessage = UIMessage<
  {
    finish_reason?: string;
    response_cut_off?: boolean;
    details?: HistoryDetail[];
  },
  {
    'seizu-detail': HistoryDetail;
  }
>;

interface ChatHistoryMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  metadata?: {
    finish_reason?: string;
    response_cut_off?: boolean;
    details?: HistoryDetail[];
  } | null;
}

interface ChatHistoryResponse {
  messages: ChatHistoryMessage[];
}

function toUIMessage(message: ChatHistoryMessage): SeizuChatHistoryMessage {
  const detailParts =
    message.role === 'assistant'
      ? (message.metadata?.details ?? []).map((detail, index) => ({
          type: 'data-seizu-detail' as const,
          id: `${message.id}-detail-${index}`,
          data: detail,
        }))
      : [];
  const textParts = [{ type: 'text' as const, text: message.text }];
  const parts = [...detailParts, ...textParts];

  return {
    id: message.id,
    role: message.role,
    metadata: message.metadata ?? undefined,
    parts,
  };
}

/**
 * Returns a stable fetcher for a chat thread's persisted messages, mapped into
 * the AI SDK UIMessage shape so they can hydrate `useChat`. Resolves to an
 * empty array when auth is required but no token is available yet, or on any
 * failure — a missing history should never block starting a conversation.
 */
export function useChatHistory(): (
  threadId: string,
) => Promise<SeizuChatHistoryMessage[]> {
  const { checkAuthReady, authHeaders } = useAuthHeaders();

  return useCallback(
    async (threadId: string): Promise<SeizuChatHistoryMessage[]> => {
      if (!checkAuthReady()) return [];
      try {
        const res = await fetch(
          `/api/v1/chat/history?thread_id=${encodeURIComponent(threadId)}`,
          { headers: authHeaders() },
        );
        if (!res.ok) return [];
        const data = (await res.json()) as ChatHistoryResponse;
        return data.messages.map(toUIMessage);
      } catch {
        return [];
      }
    },
    [authHeaders, checkAuthReady],
  );
}
