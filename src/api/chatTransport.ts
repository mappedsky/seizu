import { DefaultChatTransport, type UIMessage } from 'ai';

/**
 * Sending a message is two requests: admit the turn, then attach to it.
 *
 * The server answers admission before anything streams, so the client holds a
 * `turn_id` from the moment it sends. Everything afterwards — attaching,
 * stopping — names that id. Folding admission into the stream is what used to
 * force a second identity for the window before the first frame arrived, and a
 * way to express a stop against a turn that did not exist yet.
 *
 * An idempotency key makes admission repeatable: asking again resolves to the
 * turn already made rather than starting a second one, so a lost response is
 * fixed by retrying the same request.
 */
export type ChatTurnAdmission = {
  turn_id: string;
  status: 'created' | 'existing';
};

export type SeizuChatTransportOptions<UI_MESSAGE extends UIMessage> = {
  /** The thread being written to, read at send time rather than captured. */
  threadId: () => string | null;
  /** Bearer token, read at send time so a refresh is picked up. */
  accessToken: () => string | null;
  /** Body fields for the admission request, minus the idempotency key. */
  admissionBody: (options: {
    messages: UI_MESSAGE[];
    body?: Record<string, unknown>;
  }) => Record<string, unknown>;
  /** Called with the turn a send admitted, so Stop can name it. */
  onTurn: (turnId: string | null) => void;
};

const csrf = { 'X-Seizu-Csrf': '1' };

export class SeizuChatTransport<
  UI_MESSAGE extends UIMessage,
> extends DefaultChatTransport<UI_MESSAGE> {
  private readonly seizu: SeizuChatTransportOptions<UI_MESSAGE>;

  constructor(options: SeizuChatTransportOptions<UI_MESSAGE>) {
    super({ api: '/api/v1/chat', headers: csrf });
    this.seizu = options;
  }

  private authHeaders(): Record<string, string> {
    const token = this.seizu.accessToken();
    return {
      ...csrf,
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }

  private async attach(
    turnId: string,
    signal?: AbortSignal,
  ): Promise<ReadableStream<never>> {
    const res = await fetch(
      `/api/v1/chat/turns/${encodeURIComponent(turnId)}/stream`,
      { headers: this.authHeaders(), signal },
    );
    if (!res.ok || !res.body) {
      throw new Error('Failed to attach to the chat turn');
    }
    return this.processResponseStream(res.body) as ReadableStream<never>;
  }

  async sendMessages(
    options: Parameters<DefaultChatTransport<UI_MESSAGE>['sendMessages']>[0],
  ): ReturnType<DefaultChatTransport<UI_MESSAGE>['sendMessages']> {
    const threadId = this.seizu.threadId();
    if (!threadId) throw new Error('No conversation selected');

    const admission = await fetch(
      `/api/v1/chat/threads/${encodeURIComponent(threadId)}/turns`,
      {
        method: 'POST',
        headers: this.authHeaders(),
        signal: options.abortSignal,
        body: JSON.stringify({
          ...this.seizu.admissionBody({
            messages: options.messages,
            body: options.body as Record<string, unknown> | undefined,
          }),
          // Minted per send. Its only job is making admission idempotent — it
          // is not a way to address the turn, because admission hands back an
          // id that is.
          idempotency_key: `ik_${crypto.randomUUID().replace(/-/g, '')}`,
        }),
      },
    );
    if (!admission.ok) {
      // The server distinguishes these, and so should the message: only one of
      // them is worth retrying.
      if (admission.status === 409) {
        throw new Error('This conversation already has a turn in progress');
      }
      if (admission.status === 404) {
        throw new Error('This conversation is no longer available');
      }
      throw new Error('Could not start this turn; please try again');
    }
    const { turn_id: turnId } = (await admission.json()) as ChatTurnAdmission;
    this.seizu.onTurn(turnId);
    return this.attach(turnId, options.abortSignal);
  }

  async reconnectToStream(
    _options: Parameters<
      DefaultChatTransport<UI_MESSAGE>['reconnectToStream']
    >[0],
  ): ReturnType<DefaultChatTransport<UI_MESSAGE>['reconnectToStream']> {
    // A reloaded client has a thread but no turn id — it never saw the
    // admission. This is the one place that resolves one to the other.
    const threadId = this.seizu.threadId();
    if (!threadId) return null;
    const active = await fetch(
      `/api/v1/chat/threads/${encodeURIComponent(threadId)}/turns/active`,
      { headers: this.authHeaders() },
    );
    if (active.status === 204) {
      this.seizu.onTurn(null);
      return null;
    }
    if (!active.ok) throw new Error('Failed to look for a running turn');
    const { turn_id: turnId } = (await active.json()) as ChatTurnAdmission;
    this.seizu.onTurn(turnId);
    return this.attach(turnId);
  }
}

/** Stop a turn. Names the turn, so a late or repeated call cannot hit its successor. */
export async function cancelChatTurn(
  turnId: string,
  accessToken: string | null,
): Promise<void> {
  await fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}/cancel`, {
    method: 'POST',
    keepalive: true,
    headers: {
      ...csrf,
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
  });
}
