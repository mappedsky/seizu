# Frontend state decisions (`UI`)

Decisions behind how the React app holds state. Primary code: `src/index.tsx`,
`eslint.config.mjs`, `src/hooks/useAsyncResource.ts`.

## UI-001 — The app renders under `StrictMode`

**Applies to:** `src/index.tsx`

`<StrictMode>` wraps the root render, so development double-invokes renders and
effects.

**Why:** it was removed once because components failed under it. Those failures
were the bugs it exists to surface — impure renders and state written
synchronously from effects — not a problem with the mechanism, and removing it
left them in place while the app still paid for React's development build. The
one component already hardened for it, `AuthProvider`, carries a comment
explaining the race it would otherwise lose, which is the shape of what the
rest of the app was hiding.

**Cost:** development renders roughly double. Production is unaffected.

**Don't:** take it off to quiet a component that misbehaves under it. A
component that breaks when its effect runs twice breaks in production too, just
less often — on a remount, a fast navigation, or a replayed render.

## UI-002 — `set-state-in-effect` is an error

**Applies to:** `eslint.config.mjs`

`@eslint-react/set-state-in-effect` is `'error'`, not the plugin's default
warning.

**Why:** it catches statically what [UI-001](#ui-001-the-app-renders-under-strictmode)
catches at runtime. It stood at 133 warnings across 31 files while StrictMode
was off, which is how a whole class of "the effect will fix it on the next
render" state accumulated unnoticed. As a warning nothing failed on it, so the
count only ever went up.

**Don't:** suppress a finding with a disable comment. Every one of the 133 had
a derivation, a lazy initialiser, a `key`, or a callback ref behind it — see
[UI-003](#ui-003-state-that-follows-an-input-is-derived-from-it-not-copied-into-state).

## UI-003 — State that follows an input is derived from it, not copied into state

**Applies to:** the whole of `src/`; see `src/hooks/useAsyncResource.ts`

Where a value follows something the component already has — a prop, a route
parameter, a fetched result — it is computed during render, not written into
state by an effect watching that input. The recurring shapes are:

- **A request key.** `loading` means "the settled result is not an answer to
  the request this render is asking for", so nothing has to raise it on the way
  into an effect and a superseded request cannot leave it raised. This is what
  `useAsyncResource` packages, and what `useWorkflowRuns` already did by hand.
- **A draft with the value it was typed against.** A text buffer is shown while
  it is still a draft of what the parent holds; a parent that sets something
  else supersedes it by being read past.
- **A `key`.** A dialog form is mounted per open and keyed on the thing being
  edited, so its starting state is computed from that thing.
- **A callback ref.** A measurement is taken when the node is attached, which
  is the same point in the commit a layout effect runs at.

**Why:** copying an input into state makes two sources of truth that a render
sits between. The first render after the input changes shows the previous
value, and under StrictMode the effect that corrects it runs twice. The
concrete failure this produced: `ChatInterface` mirrored the route's thread id
into state and reconciled it from an effect, so back/forward and a freshly
admitted turn could each win; `QueryConsole` guarded its URL-restore effect with
a ref it cleared on entry, which a double-invoked effect defeats by design.

**Don't:** reintroduce a `useEffect` whose body is `setX(somethingDerivable)`.
If the value genuinely cannot be derived — it needs the clock, or a DOM
measurement — take it where it happens (a state updater, a ref callback) rather
than in an effect.
