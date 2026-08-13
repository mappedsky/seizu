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
  /**
   * The thread this send belongs to.
   *
   * The transport outlives any one conversation -- the sidebar can switch
   * sessions mid-turn -- so everything acting on the pending send checks this
   * first. Without it, thread A finishing clears thread B's pending state and
   * silently disarms Stop for the turn that is actually running.
   */
  threadId: string;
  /** The user message this send is for; retries of it reuse the key below. */
  messageId: string;
  /** Stable across retries, so a lost admission response resolves rather than
   *  starting a second turn. */
  idempotencyKey: string;
  /** Filled in when admission answers. */
  turnId: string | null;
  /** Stop pressed before we had an id. */
  stopRequested: boolean;
  /**
   * The exact body that was sent. Retained so a retry can replay *this*
   * request rather than composing a new one -- a fresh body would not match the
   * fingerprint the key is bound to, and would be refused.
   */
  body: string;
  /**
   * True once every admission attempt has failed *ambiguously* -- a 503, a
   * timeout, a dropped connection. The turn may exist server-side, so the key
   * is the only way back to it and must survive the failure.
   */
  unresolved: boolean;
};

export type SeizuChatTransportOptions<UI_MESSAGE extends UIMessage> = {
  /** The thread being written to, read at send time rather than captured. */
  threadId: () => string | null;
  /** Bearer token, read at send time so a refresh is picked up. */
  accessToken: () => string | null;
  /**
   * A stop that could not be delivered.
   *
   * Needed because a stop asked for before admission answers is carried out
   * later, by which point there is no call left to reject: `requestStop` has
   * returned, and throwing from the send is swallowed by the SDK as an expected
   * abort. Without this the exact race the deferral exists for is the one whose
   * failure is invisible.
   */
  onStopFailed: (error: unknown) => void;
  /**
   * A send's outcome became -- or stopped being -- unknown.
   *
   * The transport holds this on plain objects, which React cannot observe, so
   * without telling it nothing re-renders and the recovery the user needs is
   * never offered.
   */
  onUnresolvedChange: (threadId: string, unresolved: boolean) => void;
  /** Body fields for the admission request, minus the idempotency key. */
  admissionBody: (options: {
    messages: UI_MESSAGE[];
    body?: Record<string, unknown>;
  }) => Record<string, unknown>;
};

const csrf = { 'X-Seizu-Csrf': '1' };

/** Per-attempt deadline for admission. Short: it is one small write server-side. */
const ADMISSION_TIMEOUT_MS = 10_000;

export class SeizuChatTransport<
  UI_MESSAGE extends UIMessage,
> extends DefaultChatTransport<UI_MESSAGE> {
  private readonly seizu: SeizuChatTransportOptions<UI_MESSAGE>;
  // Keyed by thread, not a single slot. The transport outlives any one
  // conversation and the sidebar can switch mid-turn, so a single slot means
  // whichever thread acts last owns it -- and the others silently lose Stop.
  private readonly pending = new Map<string, PendingSend>();
  // The thread whose stream is currently being read. A completion callback
  // fires after the user may have switched away, so "which thread finished" is
  // not the same question as "which thread is selected".
  private streamingThread: string | null = null;

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
    // Only ever stops the conversation on screen.
    const threadId = this.seizu.threadId();
    const pending = threadId ? this.pending.get(threadId) : undefined;
    if (!pending) return;
    if (pending.turnId === null) {
      // Carried out once the turn has an id; a failure then is reported through
      // `onStopFailed`, since this call is long gone by that point.
      pending.stopRequested = true;
      return;
    }
    await cancelChatTurn(pending.turnId, this.seizu.accessToken());
  }

  /**
   * Replay a send whose outcome was never established.
   *
   * The same body under the same key, which is the only request the server can
   * resolve to the turn it may already have admitted: anything else is either a
   * second turn or a fingerprint mismatch. Returns the stream to attach to, or
   * null when there is nothing outstanding.
   */
  async retryUnresolved(): Promise<ReadableStream<never> | null> {
    const threadId = this.seizu.threadId();
    const pending = threadId ? this.pending.get(threadId) : undefined;
    if (!threadId || !pending?.unresolved || !pending.body) return null;
    const turnId = await this.admit(threadId, pending.body, pending);
    return this.attach(turnId, undefined, threadId);
  }

  /** Whether this thread has a send whose outcome is still unknown.
   *
   * The turn may exist server-side, so its key has to outlive the failure: it
   * is the only thing that can resolve to that turn rather than admitting a
   * second one.
   */
  hasUnresolvedSend(threadId: string | null): boolean {
    return threadId ? (this.pending.get(threadId)?.unresolved ?? false) : false;
  }

  /** Forget a finished turn, so a later Stop cannot land on it.
   *
   * Scoped to the thread that finished: a turn completing in a conversation the
   * user has since navigated away from must not disarm Stop for the one they
   * are now watching.
   */
  clearPending(threadId: string | null): void {
    if (threadId) this.pending.delete(threadId);
  }

  /** Forget the stream that just ended, whichever thread it belonged to.
   *
   * The caller cannot supply the thread: by the time a completion callback runs
   * the user may have switched conversations, so the currently selected one is
   * the wrong answer. The transport knows which stream it was reading.
   */
  clearFinishedStream(): void {
    const threadId = this.streamingThread;
    if (!threadId) return;
    // An unresolved send keeps its key: the turn may exist and only that key
    // can reach it.
    if (!this.pending.get(threadId)?.unresolved) this.pending.delete(threadId);
    this.streamingThread = null;
  }

  private authHeaders(): Record<string, string> {
    const token = this.seizu.accessToken();
    return {
      ...csrf,
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }

  /**
   * Ask for a turn, retrying only the outcomes that are genuinely ambiguous.
   *
   * Shared by a first send and by an explicit retry, so both carry the same key
   * and the same body -- which is what makes the server able to resolve the
   * second to whatever the first may already have admitted.
   */
  private async admit(
    threadId: string,
    body: string,
    pending: PendingSend,
  ): Promise<string> {
    // Deliberately not given the SDK's abort signal. Aborting this request does
    // not un-admit the turn -- the server may already have started it -- so an
    // abort here would strand a running turn the client has no id for. Stop is
    // honoured through `requestStop` instead.
    let admission: Response | null = null;
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (attempt > 0) {
        await new Promise((resolve) => setTimeout(resolve, 150 * attempt));
      }
      // A deadline of its own: without one a response that never arrives pins
      // the send forever, so the loop cannot advance and a stop asked for in
      // the meantime is never delivered. A timeout is ambiguous in exactly the
      // way a 503 is, and retries with the same key.
      const deadline = new AbortController();
      const timer = setTimeout(() => deadline.abort(), ADMISSION_TIMEOUT_MS);
      try {
        admission = await fetch(
          `/api/v1/chat/threads/${encodeURIComponent(threadId)}/turns`,
          {
            method: 'POST',
            headers: this.authHeaders(),
            body,
            signal: deadline.signal,
          },
        );
      } catch (error) {
        lastError = error;
        continue;
      } finally {
        clearTimeout(timer);
      }
      // Only 503 is ambiguous. A 409/404 is a decision, and repeating it just
      // asks the same question again.
      if (admission.status !== 503) break;
    }

    if (admission === null || admission.status === 503) {
      // Never established. Keep the key: replaying with it is the only route
      // back to a turn the server may already have admitted.
      pending.unresolved = true;
      this.seizu.onUnresolvedChange(threadId, true);
      if (pending.stopRequested) {
        this.seizu.onStopFailed(
          new Error('Could not confirm the turn was stopped'),
        );
      }
      throw lastError instanceof Error
        ? lastError
        : new Error('Could not start this turn; please try again');
    }
    if (!admission.ok) {
      // A decision, so the key is spent.
      pending.unresolved = false;
      this.seizu.onUnresolvedChange(threadId, false);
      if (pending.stopRequested) {
        this.seizu.onStopFailed(
          new Error('Could not confirm the turn was stopped'),
        );
      }
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
    pending.unresolved = false;
    this.seizu.onUnresolvedChange(threadId, false);
    if (pending.stopRequested) {
      // Stop arrived while this was in flight. The turn exists now, so it can
      // finally be told.
      try {
        await cancelChatTurn(turnId, this.seizu.accessToken());
      } catch (error) {
        // Never thrown from here: the SDK has already seen its abort and treats
        // anything this throws as expected, so it would vanish.
        this.seizu.onStopFailed(error);
      }
    }
    return turnId;
  }

  private async attach(
    turnId: string,
    signal?: AbortSignal,
    threadId?: string,
  ): Promise<ReadableStream<never>> {
    if (threadId) this.streamingThread = threadId;
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
    const existing = this.pending.get(threadId);
    const pending: PendingSend =
      existing?.messageId === messageId && existing.turnId === null
        ? existing
        : {
            threadId,
            messageId,
            idempotencyKey: `ik_${crypto.randomUUID().replace(/-/g, '')}`,
            turnId: null,
            stopRequested: false,
            unresolved: false,
            body: '',
          };
    const body = JSON.stringify({
      ...this.seizu.admissionBody({
        messages: options.messages,
        body: options.body as Record<string, unknown> | undefined,
      }),
      idempotency_key: pending.idempotencyKey,
    });
    // Retained so a retry can replay *this* request: a recomposed body would
    // not match the fingerprint the key is bound to, and would be refused.
    pending.body = body;
    this.pending.set(threadId, pending);

    const turnId = await this.admit(threadId, body, pending);
    return this.attach(turnId, options.abortSignal, threadId);
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
      // This thread only. Clearing the whole slot used to disarm Stop for a
      // conversation the user had switched away from but left running.
      this.pending.delete(threadId);
      return null;
    }
    if (!active.ok) throw new Error('Failed to look for a running turn');
    const { turn_id: turnId } = (await active.json()) as ChatTurnAdmission;
    // A turn this tab did not start is still one it can stop.
    this.pending.set(threadId, {
      threadId,
      messageId: `reconnect:${turnId}`,
      idempotencyKey: '',
      body: '',
      turnId,
      stopRequested: false,
      unresolved: false,
    });
    return this.attach(turnId, undefined, threadId);
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
