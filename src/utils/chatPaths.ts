// Where chat's own pages live. Shared because the conversation, the sessions
// panel and the connections page all have to agree on them.

export const CHAT_LANDING_PATH = '/app/chat';
const CHAT_CONNECTIONS_PATH = '/app/chat/connections';
/** Names the conversation a chat-wide page was opened from. */
export const CHAT_RETURN_PARAM = 'from';

export function chatSessionPath(threadId: string): string {
  return `/app/chat/${encodeURIComponent(threadId)}`;
}

/** Connections, carrying the conversation it was opened from.
 *
 * In the URL rather than in router state: the page is where someone follows a
 * gateway's authorization link and comes back, and a reload must not cost them
 * the way back to what they were asking.
 */
export function chatConnectionsPath(threadId: string | null): string {
  return threadId
    ? `${CHAT_CONNECTIONS_PATH}?${CHAT_RETURN_PARAM}=${encodeURIComponent(threadId)}`
    : CHAT_CONNECTIONS_PATH;
}

/** Where a chat-wide page goes back to: the conversation it was opened from,
 *  or the landing when it was reached directly. */
export function chatReturnPath(threadId: string | null): string {
  return threadId ? chatSessionPath(threadId) : CHAT_LANDING_PATH;
}
