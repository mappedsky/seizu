# Walkthrough GIF Recording

The animated GIFs in the splash-page carousel
(`docs/root/_templates/splash.html`) are recorded by driving a real Chrome over
the DevTools Protocol. The harness lives in `scripts/walkthroughs/`; the clips
themselves are the six files in `scripts/walkthroughs/clips/`.

```bash
make walkthroughs                                  # all clips
make walkthroughs CLIPS="chat workflows"           # a subset
make walkthroughs CDP_HOST=192.168.5.2:9222        # a browser on another host
```

Output goes straight to `images/*.gif`, which Sphinx exposes under `_static/`
(`html_static_path = ["images", "_static"]` in `docs/conf.py`). Frames are
scratch and land in `.walkthroughs/`, which is gitignored.

## What has to be true first

- **Chrome running with `--remote-debugging-port=9222`**, reachable at
  `CDP_HOST` (default `127.0.0.1:9222`). Confirm with
  `curl -s http://$CDP_HOST/json/version`.
- **The browser window visible on an awake display.** This is the one that
  bites. Chrome does not composite a hidden tab, and `Page.startScreencast`
  then emits *nothing* — not a single frame — whether the tab is behind another
  window, on another Space, or on a sleeping display. Neither
  `Page.bringToFront` nor `Target.activateTarget` overrides it. The harness
  detects this, warns, and falls back to `Page.captureScreenshot` polling, but
  polling costs a round trip per frame (~2 fps at this size against ~17 fps for
  the screencast) and the result looks like a slideshow. If a run logs
  `recording (polling)`, stop and fix the window before keeping the output.
- **The dev stack up** at `SEIZU_URL` (default `http://localhost:3000`) with
  seeded data — `make seed_dashboard`. The clips reference the seeded `Panel
  Examples` report, the `GitHub Security` toolset, the
  `github-security-investigations` plugin, and the seeded workflows.
- **Docker**, for ffmpeg. There is no ffmpeg on the host; encoding runs in
  `linuxserver/ffmpeg`.
- **Node 22+** for the built-in `WebSocket`. The harness has no dependencies —
  nothing to install.

Two clips write to the dev stack rather than only reading it:

- **`clips/chat.mjs` makes a real model call** against whatever `CHAT_LLM_MODEL`
  is configured, so it costs money and takes as long as the model takes — on the
  reference stack, about two minutes. It follows the turn through to its answer
  (`waitForGone('is working')`) and holds there.
- **`clips/workflows.mjs` builds a workflow and saves it.** It deletes any
  earlier `Critical CVE alerting` first, so re-recording does not stack up
  duplicates, and leaves the new one behind.

## How the harness works

- `cdp.mjs` — one browser-level WebSocket driving every tab through flat
  sessions.
- `record.mjs` — captures frames and encodes them. Both capture modes write the
  same `frames/*.jpg` plus a concat list carrying **real durations**, so a pause
  in the UI stays a pause in the GIF rather than being resampled away.
- `harness.mjs` — the `clip()` runner and the step API.

Three details worth knowing before writing a clip:

- **Clicks are real input events.** `Runtime.evaluate` with `.click()` does not
  work: it finds the wrapper `<li>` and React Router never fires. Steps dispatch
  `Input.dispatchMouseEvent` at the element's measured centre.
- **Coordinates are viewport coordinates**, so `hover` scrolls a target into
  view before clicking — otherwise the click lands on whatever happens to be at
  those coordinates. It checks `elementFromPoint` rather than only the viewport
  bounds, because a scrollable dialog is shorter than the window: a control at
  the bottom of the New Workflow dialog is inside the viewport and still behind
  the backdrop, and clicking it closes the dialog.
- **A panel can swallow the wheel.** A graph panel zooms itself on wheel events,
  so a wheel aimed into one scrolls nothing. `scrollBy` wheels clear of the
  panels, checks the page actually moved, and finishes with a smooth
  `window.scrollBy` if it did not. Prefer `wheelToElement`, which locates a
  section by its heading — filtering a report changes its height, so absolute
  offsets do not survive.
- **The cursor is synthetic.** The screencast does not capture the OS pointer,
  so the harness injects an SVG arrow and a click ripple, re-injected on every
  navigation.

Selector gotchas, all of which have already cost a re-record:

- `text=` matching is **case-insensitive**: MUI uppercases buttons and tabs in
  CSS, so the DOM text is `Run` where the screen reads `RUN`.
- Scope dialog buttons with `within: '[role="dialog"]'`. The report edit toolbar
  has its own **Cancel**, and clicking that leaves edit mode instead of closing
  the dialog.
- Report filter inputs are addressed by `id` (`#panel_examples_search`), not
  `name` or `aria-label`.
- Use `label=` for form fields: a MUI Select has no `htmlFor` on its label, so
  nothing else links the two. Use `has=<selector>::<substring>` for rows whose
  text carries more than the part worth addressing, like a query-history entry
  that ends in a timestamp.
- Scope dropdown options with `within: '[role="listbox"]'`. Options render in a
  portal, and an element of the same name elsewhere on the page will otherwise
  win and then fail the hit test.
- Detail dialogs close with an icon button, not a text one — send `Escape`.
- The user-defined toolsets sort after the built-ins and fall on page 2, so the
  toolsets clip searches rather than paginates.

## The clips

| File | GIF | Shows |
|------|-----|-------|
| `clips/reports.mjs` | `seizu-report-walkthrough.gif` | A slow pass down Panel Examples, changing each filter while the panels it drives are on screen, and selecting a node in the graph panel |
| `clips/edit-report.mjs` | `seizu-edit-report-walkthrough.gif` | Edit mode: named Cypher, the Edit input dialog, the Edit panel dialog with the Markdown editor |
| `clips/query-console.mjs` | `seizu-query-console-walkthrough.gif` | Schema browser, a discovery query, node selection with its properties, Graph/Table/Raw, then recalling a query from history and running it |
| `clips/workflows.mjs` | `seizu-workflows-walkthrough.gif` | Building a two-stage workflow: a query stage feeding a second stage that fans out to Slack and statsd in parallel |
| `clips/toolsets-plugins.mjs` | `seizu-toolsets-plugins-walkthrough.gif` | MCP toolsets, a tool's Cypher and parameters, a plugin package's skills, then the staged package editor |
| `clips/chat.mjs` | `seizu-chat-walkthrough.gif` | Asking the graph a question and following the turn to its answer, with routing, thinking and tool calls in the details pane |

`seizu-mcp-agent-walkthrough.gif` is **not** produced here. It is a terminal
capture of Claude Code using Seizu's MCP tools, with hand-drawn redaction bars,
and has to be remade by hand if it ever goes stale.

`clips/edit-report.mjs` deliberately ends on **Cancel**. Saving would add a
version to the seeded report on every re-record; the clip shows the editing
surface without mutating anything.

## Output settings

1568x727 at 1:1 — the capture viewport matches the output width, so nothing is
resampled — 10 fps, 96-colour palette with `stats_mode=diff` and Bayer
dithering. Raising the palette to 128 colours costs about 20% more bytes for no
visible gain on this dark UI; dropping to 64 saves a little more but risks
banding in the chart panels.

Two per-clip knobs control length, and they are not the same thing:

- `speed` divides every frame's duration, so a long build plays briskly without
  losing any of it. The workflow clip runs at 1.7.
- `maxFrameSeconds` caps how long a *single* held frame lasts. This is what
  shortens a clip that spends a minute waiting on a model: it removes the dead
  air and leaves the rest at its own pace.

The chat clip needs both, plus `fps: 6` and `colors: 64`. Streaming text changes
on every frame, which is the most expensive thing a GIF can encode — at the
defaults that clip alone is 15 MB against 1-5 MB for every other one.
