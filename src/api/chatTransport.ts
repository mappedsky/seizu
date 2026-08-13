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

/**
 * One logical send, from before it has a turn until that turn is finished.
 *
 * Stop is enabled from the moment a message is submitted, which is *before*
 * admission answers, so there is a window where the user can ask to stop a turn
 * the client cannot yet name. Aborting the admission request does not close it:
 * the server may already have admitted and started the turn, which then runs
 * with nobody watching or stopping it. Recording the intent instead means the
 * stop is applied the moment the turn has an id.
 */
type PendingSend = {
  /** The user message this send is for; retries of it reuse the key below. */
  messageId: string;
  /** Stable across retries, so a lost admission response resolves rather than
   *  starting a second turn. */
  idempotencyKey: string;
  /** Filled in when admission answers. */
  turnId: string | null;
  /** Stop pressed before we had an id. */
  stopRequested: boolean;
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
};

const csrf = { 'X-Seizu-Csrf': '1' };

export class SeizuChatTransport<
  UI_MESSAGE extends UIMessage,
> extends DefaultChatTransport<UI_MESSAGE> {
  private readonly seizu: SeizuChatTransportOptions<UI_MESSAGE>;
  private pending: PendingSend | null = null;

  constructor(options: SeizuChatTransportOptions<UI_MESSAGE>) {
    super({ api: '/api/v1/chat', headers: csrf });
    this.seizu = options;
  }

  /**
   * Stop the turn this client is watching, whatever stage it is at.
   *
   * Three states, one entry point: a turn we can name is cancelled now; a send
   * still waiting on admission is marked so it is cancelled the instant it has
   * an id; and with neither there is nothing running to stop.
   */
  async requestStop(): Promise<void> {
    const pending = this.pending;
    if (!pending) return;
    if (pending.turnId === null) {
      pending.stopRequested = true;
      return;
    }
    await cancelChatTurn(pending.turnId, this.seizu.accessToken());
  }

  /** Forget the finished turn, so a later Stop cannot land on it. */
  clearPending(): void {
    this.pending = null;
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

    // One key per logical message, reused while that message has no turn yet.
    // Minting a fresh one per attempt would make the server's idempotency
    // promise unreachable from here: a retry would admit a *second* turn rather
    // than resolving to the one a lost response already created.
    const messageId =
      options.messageId ?? options.messages.at(-1)?.id ?? crypto.randomUUID();
    const pending: PendingSend =
      this.pending?.messageId === messageId && this.pending.turnId === null
        ? this.pending
        : {
            messageId,
            idempotencyKey: `ik_${crypto.randomUUID().replace(/-/g, '')}`,
            turnId: null,
            stopRequested: false,
          };
    this.pending = pending;

    // Deliberately not given `options.abortSignal`. Aborting this request does
    // not un-admit the turn -- the server may already have started it -- so an
    // abort here would strand a running turn the client has no id for. Stop is
    // honoured through `requestStop` below instead.
    const admission = await fetch(
      `/api/v1/chat/threads/${encodeURIComponent(threadId)}/turns`,
      {
        method: 'POST',
        headers: this.authHeaders(),
        body: JSON.stringify({
          ...this.seizu.admissionBody({
            messages: options.messages,
            body: options.body as Record<string, unknown> | undefined,
          }),
          idempotency_key: pending.idempotencyKey,
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
    pending.turnId = turnId;
    if (pending.stopRequested) {
      // Stop arrived while this was in flight. The turn exists now, so it can
      // finally be told -- then attach anyway, so the client reads the turn's
      // own closing frames rather than guessing how it ended.
      await cancelChatTurn(turnId, this.seizu.accessToken());
    }
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
      this.pending = null;
      return null;
    }
    if (!active.ok) throw new Error('Failed to look for a running turn');
    const { turn_id: turnId } = (await active.json()) as ChatTurnAdmission;
    // A turn this tab did not start is still one it can stop.
    this.pending = {
      messageId: `reconnect:${turnId}`,
      idempotencyKey: '',
      turnId,
      stopRequested: false,
    };
    return this.attach(turnId);
  }
}

/** Stop a turn. Names the turn, so a late or repeated call cannot hit its successor. */
export async function cancelChatTurn(
  turnId: string,
  accessToken: string | null,
): Promise<void> {
  const res = await fetch(
    `/api/v1/chat/turns/${encodeURIComponent(turnId)}/cancel`,
    {
      method: 'POST',
      keepalive: true,
      headers: {
        ...csrf,
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
    },
  );
  // The reader stops either way, so an unchecked failure here looks exactly
  // like success while the turn keeps generating and running queued actions.
  if (!res.ok) {
    throw new Error(`Failed to stop the chat turn (${res.status})`);
  }
}
