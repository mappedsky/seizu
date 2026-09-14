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

## UI-004 — The browser tab is named by `PageTitle`, not a head-management library

**Applies to:** `src/components/PageTitle.tsx`

All 26 call sites used `react-helmet` for a `<title>` and nothing else — no
`meta`, `link` or `script`. It is replaced by a component that sets
`document.title` from an effect and restores what `index.html` declared when
the last one unmounts.

**Why:** `react-helmet` is built on `react-side-effect`, which registers in
`UNSAFE_componentWillMount`. Under [UI-001](#ui-001-the-app-renders-under-strictmode)
that logs `Using UNSAFE_componentWillMount in strict mode is not recommended …
SideEffect(NullComponent)` on every page. The package has not shipped since
2020, so it will not be fixed, and swapping to a maintained fork would have
kept a dependency whose entire remaining job is one assignment to
`document.title`.

**Restoring on unmount is load-bearing,** not tidiness: around a dozen routes
(the query console, chat, toolsets, roles, …) set no title at all, so without
it the tab would keep the previous page's title after navigating to one of
them.

**Exactly one may be mounted at a time,** which is why `ReportView` takes a
`documentTitle` prop instead of a page rendering a second one beside it. Helmet
resolved two claims by "innermost wins", an order it got from registering
during render; effects run child-first, so the naive replacement silently
inverts that. Rather than depend on an ordering React does not promise, the
component warns in development when a second one mounts, and the two call sites
that nested (`SpaceDetail`, `ReportVersionView`) each name one owner. Fixing
those also revived `ReportVersionView`'s version-qualified title, which the
nested `ReportView` had been overwriting since it was written.

**Don't:** reintroduce a head library for a title, or render a `PageTitle`
inside a component that a page may also title.

## UI-005 — The query console's run is state, and the address bar is an event

**Applies to:** `src/pages/QueryConsole.tsx`

What the console is running — a typed query or a stored history entry — is
explicit state changed only by what the user did. `location` is deliberately
absent from the render-time derivation. A completed query publishes its
`?h=<id>` and records the id as one of its own; the address bar re-enters the
console only through a single effect, which adopts a `?h=` the page did not
publish and does so once per URL.

**Why:** deriving the run from the URL means re-deriving it while the page's own
`navigate` is still settling. `navigate` reaches the router through its history
listener, a commit after the state set beside it, and in that window the URL and
the run disagree — which read as "the user asked for a history entry", ran it,
published a new URL, and raced again. Measured in the dev database: one
schema-panel query re-executed **222 times, once every ~2.5 seconds** — the
query's own duration, because each run was triggered by the previous one
finishing. It never settled, so nothing was ever displayed.

**Why an effect here, against [UI-002](#ui-002-set-state-in-effect-is-an-error):**
the history stack is an external system, not a value this render can compute,
which is the case [UI-003](#ui-003-state-that-follows-an-input-is-derived-from-it-not-copied-into-state)
leaves to an effect. It carries the only `set-state-in-effect` disable in the
codebase, and it is narrow: one URL, adopted once.

**Both guards are refs that are never cleared on read** — the set of ids this
page published, and the last URL the restore acted on. That is the difference
from the `justPushedRef` this replaces, which cleared its flag as it read it and
so was defeated by StrictMode's second pass ([UI-001](#ui-001-the-app-renders-under-strictmode)).

**Don't:** compare the URL against the last one the page navigated to. It is
only *eventually* equal, and the render in between is the bug.

## UI-006 — A feature configures itself at the foot of its own panel

**Applies to:** `src/components/ChatSessionsPanel.tsx`,
`src/components/SpaceReportsPanel.tsx`, `src/components/DashboardSidebar.tsx`

Settings that belong to a whole feature sit in a bordered footer group at the
bottom of that feature's panel — a space's sub-space and report actions, chat's
per-user gateway connections. The main sidebar names product areas and the
reports pinned to them; it is not where a feature's own configuration goes.

**Why:** "Chat Connections" as a top-level entry read as a peer of Chat, Spaces
and Workflows, and it is not one — it configures chat, and the page it opens
already carries a *Back to Chat* button. Reaching it meant leaving the
conversation through navigation that never mentioned the conversation. The
space panel had already settled the shape, so chat's version is the same one
rather than a second idea.

**The other axis is the turn, and it is not this one.** The confirmations pane
is about the turn on screen, so it stays beside the transcript; the panel footer
is for what outlives any single conversation. A new chat surface belongs to
whichever of those it is about.

**Collapsed panels keep the entry**, as an icon with its tooltip: the footer is
how the setting is reached at all now, so hiding it behind reopening the panel
would make it harder to find than the sidebar entry it replaced.
