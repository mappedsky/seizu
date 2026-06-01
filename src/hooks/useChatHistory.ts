import { useCallback } from 'react';
import type { UIMessage } from 'ai';
import { useAuthHeaders } from 'src/hooks/useAuthHeaders';

interface ChatHistoryMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

interface ChatHistoryResponse {
  messages: ChatHistoryMessage[];
}

function toUIMessage(message: ChatHistoryMessage): UIMessage {
  return {
    id: message.id,
    role: message.role,
    parts: [{ type: 'text', text: message.text }],
  };
}

/**
 * Returns a stable fetcher for a chat thread's persisted messages, mapped into
 * the AI SDK UIMessage shape so they can hydrate `useChat`. Resolves to an
 * empty array when auth is required but no token is available yet, or on any
 * failure — a missing history should never block starting a conversation.
 */
export function useChatHistory(): (threadId: string) => Promise<UIMessage[]> {
  const { checkAuthReady, authHeaders } = useAuthHeaders();

  return useCallback(
    async (threadId: string): Promise<UIMessage[]> => {
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
