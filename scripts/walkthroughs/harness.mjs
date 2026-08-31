// Clip harness: one screencast per walkthrough GIF, with a synthetic cursor
// (Chrome's screencast does not capture the real pointer) and a click ripple.
import { connect, page, goto, sleep, isVisible } from './cdp.mjs';
import { record, toGif } from './record.mjs';
import { mkdir } from 'node:fs/promises';

// Frames are scratch; only the .gif is kept, next to the other repo images.
const OUT_DIR = process.env.WALKTHROUGH_OUT || `${process.cwd()}/.walkthroughs`;
export const IMAGES_DIR = process.env.WALKTHROUGH_IMAGES || `${process.cwd()}/images`;

export const VIEWPORT = { width: 1568, height: 727 };

// Wheel here: clear of report panels, which capture wheel events of their own.
const WHEEL_X = 300;
const WHEEL_Y = 600;
export const BASE = process.env.SEIZU_URL || 'http://localhost:3000';

const CURSOR_JS = `(() => {
  if (window.__seizuCursor) return 'exists';
  const el = document.createElement('div');
  el.id = 'seizu-rec-cursor';
  el.style.cssText = [
    'position:fixed','left:0','top:0','width:22px','height:22px',
    'pointer-events:none','z-index:2147483647','will-change:transform',
    'transition:transform 40ms linear',
  ].join(';');
  el.innerHTML =
    '<svg viewBox="0 0 22 22" width="22" height="22">' +
    '<path d="M3 2 L3 17 L7.2 13.2 L9.8 19.2 L12.6 18 L10 12.2 L15.6 12.1 Z"' +
    ' fill="#fff" stroke="#0a0f22" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  document.documentElement.appendChild(el);
  const ripple = document.createElement('div');
  ripple.id = 'seizu-rec-ripple';
  ripple.style.cssText = [
    'position:fixed','left:0','top:0','width:0','height:0','border-radius:50%',
    'pointer-events:none','z-index:2147483646','opacity:0',
    'border:2px solid rgba(143,180,255,0.95)','background:rgba(143,180,255,0.18)',
  ].join(';');
  document.documentElement.appendChild(ripple);
  window.__seizuCursor = { el, ripple };
  window.__seizuCursorTo = (x, y) => { el.style.transform = 'translate(' + x + 'px,' + y + 'px)'; };
  window.__seizuCursorClick = (x, y) => {
    ripple.animate(
      [ { width:'0px', height:'0px', opacity:0.9, transform:'translate(' + x + 'px,' + y + 'px)' },
        { width:'42px', height:'42px', opacity:0, transform:'translate(' + (x-21) + 'px,' + (y-21) + 'px)' } ],
      { duration: 450, easing: 'ease-out' });
  };
  return 'installed';
})()`;

export async function installCursor(p) {
  await p.send('Runtime.evaluate', { expression: CURSOR_JS });
  // Survive SPA route changes and reloads.
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: CURSOR_JS });
}

export function clipPage(p) {
  const mouse = { x: VIEWPORT.width / 2, y: VIEWPORT.height / 2 };

  const setCursor = (x, y) =>
    p.send('Runtime.evaluate', { expression: `window.__seizuCursorTo && window.__seizuCursorTo(${x},${y})` });

  const api = {
    p,
    sleep,
    async go(path) {
      await goto(p, `${BASE}${path}`);
      await installCursor(p);
      await setCursor(mouse.x, mouse.y);
    },
    // Steps run against a SPA that is still fetching, so a miss means "not yet"
    // far more often than "not there". Poll before giving up.
    async rect(selectorOrText, { index = 0, within, waitMs = 9000, scroll = false } = {}) {
      const deadline = Date.now() + waitMs;
      for (;;) {
        const hit = await api.resolve(selectorOrText, { index, within, scroll });
        if (hit) return hit;
        if (Date.now() > deadline) return null;
        await sleep(300);
      }
    },
    // One resolver for both measuring and scrolling, so they can never
    // disagree about which element a step means.
    async resolve(selectorOrText, { index = 0, within, scroll = false } = {}) {
      const { result } = await p.send('Runtime.evaluate', { returnByValue: true, expression: `
        (() => {
          const spec = ${JSON.stringify(selectorOrText)};
          const withinSpec = ${JSON.stringify(within ?? null)};
          let root = document;
          if (withinSpec) {
            root = document.querySelector(withinSpec);
            if (!root) return null;          // scope missing means not ready yet
          }
          let els = [];
          if (spec.startsWith('text=')) {
            // Case-insensitive: MUI uppercases buttons and tabs in CSS, so the
            // DOM text is 'Run' where the screen reads 'RUN'.
            const want = spec.slice(5).toLowerCase();
            const hit = (e) => e.textContent.trim().toLowerCase() === want && e.getClientRects().length;
            els = [...root.querySelectorAll('*')].filter(e => e.children.length === 0 && hit(e));
            if (!els.length) els = [...root.querySelectorAll('a,button,[role="button"],[role="tab"],[role="menuitem"],li,td,th')].filter(hit);
          } else if (spec.startsWith('has=')) {
            // 'has=<selector>::<substring>' — for rows whose text carries more
            // than the part worth addressing, like a query plus its timestamp.
            const cut = spec.indexOf('::');
            const sel = spec.slice(4, cut);
            const want = spec.slice(cut + 2);
            els = [...root.querySelectorAll(sel)].filter(
              (e) => e.textContent.includes(want) && e.getClientRects().length);
          } else if (spec.startsWith('label=')) {
            // The field under a given form label. A MUI Select has no htmlFor
            // on its label, so nothing else links the label to the control.
            const want = spec.slice(6).toLowerCase();
            els = [...root.querySelectorAll('.MuiFormControl-root')]
              .filter((fc) => {
                const l = fc.querySelector('label');
                if (!l) return false;
                // Trailing '*' marks a required field and is not part of the label.
                const text = l.textContent.trim().replace('*', '').trim().toLowerCase();
                return text === want;
              })
              .map((fc) => fc.querySelector('[role="combobox"], input, textarea'))
              .filter((e) => e && e.getClientRects().length);
          } else {
            els = [...root.querySelectorAll(spec)].filter(e => e.getClientRects().length);
          }
          const el = els[${index}];
          if (!el) return null;
          if (${scroll ? 'true' : 'false'}) el.scrollIntoView({ block: 'center' });
          const r = el.getBoundingClientRect();
          const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
          // A scrollable dialog can be shorter than the window, so being inside
          // the viewport is not the same as being clickable: check that a click
          // here would actually reach this element and not a backdrop over it.
          const atPoint = document.elementFromPoint(x, y);
          const hittable = !!atPoint && (atPoint === el || el.contains(atPoint) || atPoint.contains(el));
          return { x, y, w: Math.round(r.width), h: Math.round(r.height), hittable };
        })()` });
      return result.value;
    },
    async move(x, y, { steps = 14 } = {}) {
      const from = { ...mouse };
      for (let i = 1; i <= steps; i++) {
        const e = i / steps;
        const k = e < 0.5 ? 2 * e * e : 1 - Math.pow(-2 * e + 2, 2) / 2; // ease-in-out
        const nx = Math.round(from.x + (x - from.x) * k);
        const ny = Math.round(from.y + (y - from.y) * k);
        await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: nx, y: ny, button: 'none' });
        await setCursor(nx, ny);
        await sleep(18);
      }
      mouse.x = x; mouse.y = y;
    },
    async hover(target, opts = {}) {
      if (typeof target !== 'string') {
        if (!target) throw new Error('hover: no element');
        await api.move(target.x, target.y);
        return target;
      }
      let r = await api.rect(target, opts);
      if (!r) throw new Error(`hover: no element for ${target}`);
      // A click is dispatched at viewport coordinates, so an off-screen target
      // would land on whatever happens to be at those coordinates instead.
      // Near the bottom edge a hit test can still succeed on an element the
      // click will not reach, so treat the lower band as needing a scroll too.
      if (!r.hittable || r.y > VIEWPORT.height - 90) {
        await api.rect(target, { ...opts, scroll: true, waitMs: 2000 });
        await sleep(700);
        r = await api.rect(target, opts);
        if (!r) throw new Error(`hover: ${target} vanished after scrolling`);
        if (!r.hittable) {
          throw new Error(`hover: ${target} is covered even after scrolling`);
        }
      }
      await api.move(r.x, r.y);
      return r;
    },
    async click(target, opts = {}) {
      const r = await api.hover(target, opts);
      await sleep(160);
      await p.send('Runtime.evaluate', { expression: `window.__seizuCursorClick && window.__seizuCursorClick(${r.x},${r.y})` });
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await sleep(70);
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
      await sleep(opts.settle ?? 900);
      return r;
    },
    // execCommand still works on a focused field and, unlike a synthetic
    // Cmd+A, does not depend on which platform's shortcut Chrome expects.
    async selectAll() {
      await p.send('Runtime.evaluate', { expression: `document.execCommand('selectAll')` });
      await sleep(150);
    },
    async type(text, { cps = 20 } = {}) {
      for (const ch of text) {
        await p.send('Input.insertText', { text: ch });
        await sleep(1000 / cps + Math.random() * 30);
      }
    },
    async key(name, modifiers = 0) {
      const map = {
        Enter: { windowsVirtualKeyCode: 13, key: 'Enter', text: '\r' },
        Escape: { windowsVirtualKeyCode: 27, key: 'Escape' },
        Backspace: { windowsVirtualKeyCode: 8, key: 'Backspace' },
      };
      const k = map[name] || { key: name };
      await p.send('Input.dispatchKeyEvent', { type: 'keyDown', modifiers, ...k });
      await p.send('Input.dispatchKeyEvent', { type: 'keyUp', modifiers, ...k });
    },
    // Wheel scrolling repaints, which the screencast picks up. JS scrollTo does not.
    async wheel(dy, { x = 900, y = 420, steps = 14 } = {}) {
      for (let i = 0; i < steps; i++) {
        await p.send('Input.dispatchMouseEvent', {
          type: 'mouseWheel', x, y, deltaX: 0, deltaY: dy / steps });
        await sleep(28);
      }
      await sleep(250);
    },
    // Wheel to an absolute scroll position. Wheeling rather than scrollTo keeps
    // the repaints the screencast needs, and the easing reads as a hand.
    async wheelTo(targetY, { steps = 26 } = {}) {
      const { result } = await p.send('Runtime.evaluate', {
        returnByValue: true, expression: 'window.scrollY' });
      const from = result.value || 0;
      const delta = targetY - from;
      if (Math.abs(delta) < 4) return;
      for (let i = 0; i < steps; i++) {
        await p.send('Input.dispatchMouseEvent', {
          type: 'mouseWheel', x: 900, y: 420, deltaX: 0, deltaY: delta / steps });
        await sleep(26);
      }
      await sleep(300);
    },
    // Scroll until a given element sits `top` pixels down the viewport.
    // Filtering a report changes its height, so absolute offsets do not hold.
    async wheelToElement(selector, { top = 230, opts = {} } = {}) {
      const r = await api.rect(selector, opts);
      if (!r) throw new Error(`wheelToElement: no element for ${selector}`);
      await api.scrollBy(r.y - r.h / 2 - top);
    },
    // Wheel by a delta, checking that the page actually moved. A graph panel
    // swallows wheel events to zoom itself, so a wheel aimed into one scrolls
    // nothing and silently zooms the graph instead.
    async scrollBy(delta, { steps = 26 } = {}) {
      if (Math.abs(delta) < 6) return;
      const scrollY = async () => {
        const { result } = await p.send('Runtime.evaluate', {
          returnByValue: true, expression: 'window.scrollY' });
        return result.value || 0;
      };
      const before = await scrollY();
      for (let i = 0; i < steps; i++) {
        await p.send('Input.dispatchMouseEvent', {
          type: 'mouseWheel', x: WHEEL_X, y: WHEEL_Y, deltaX: 0, deltaY: delta / steps });
        await sleep(26);
      }
      await sleep(260);
      const moved = (await scrollY()) - before;
      if (Math.abs(moved) < Math.abs(delta) * 0.5) {
        // Land it anyway; smooth so the screencast still sees the travel.
        await p.send('Runtime.evaluate', {
          expression: `window.scrollBy({ top: ${delta - moved}, behavior: 'smooth' })` });
        await sleep(900);
      }
    },
    async waitForText(text, timeout = 30000) {
      const started = Date.now();
      for (;;) {
        const { result } = await p.send('Runtime.evaluate', { returnByValue: true,
          expression: `document.body.innerText.includes(${JSON.stringify(text)})` });
        if (result.value) return true;
        if (Date.now() - started > timeout) return false;
        await sleep(400);
      }
    },
    async waitForGone(text, timeout = 60000) {
      const started = Date.now();
      for (;;) {
        const { result } = await p.send('Runtime.evaluate', { returnByValue: true,
          expression: `document.body.innerText.includes(${JSON.stringify(text)})` });
        if (!result.value) return true;
        if (Date.now() - started > timeout) return false;
        await sleep(400);
      }
    },
  };
  return api;
}

async function sweepOldRuns(name) {
  const { readdir, rm } = await import('node:fs/promises');
  try {
    const runs = await readdir(`${OUT_DIR}/${name}`);
    for (const run of runs.slice(0, -1)) {
      await rm(`${OUT_DIR}/${name}/${run}`, { recursive: true, force: true }).catch(() => {});
    }
  } catch {
    /* nothing recorded here before */
  }
}

export async function clip(name, { start, fps = 10, colors = 96, dither = 'bayer:bayer_scale=5', speed = 1, maxFrameSeconds = 2 }, steps) {
  const dir = `${OUT_DIR}/${name}/${Date.now()}`;
  await mkdir(dir, { recursive: true });
  const cdp = await connect();
  // 1:1 capture at the output size: no downscale blur, and half the bytes
  // per polled frame.
  const p = await page(cdp, { url: new URL(BASE).host, ...VIEWPORT, deviceScaleFactor: 1 });
  await p.send('Page.bringToFront');
  if (!(await isVisible(p))) {
    console.warn(
      `[${name}] the tab is hidden — Chrome is not compositing it, so this will ` +
      'fall back to screenshot polling (~2 fps). Bring the browser window to the ' +
      'front on an awake display and re-run for a full-rate capture.',
    );
  }
  const log = (m) => console.log(`[${name}] ${m}`);
  const c = clipPage(p);
  log('navigating');
  await c.go(start);
  await sleep(2500);

  const rec = await record(cdp, p, `${dir}/frames`, { speed, maxFrameSeconds });
  log(`recording (${rec.mode})`);
  await sleep(900);
  try {
    await steps(c);
  } catch (err) {
    console.error('STEP FAILED:', err.message);
  }
  await sleep(1100);
  log('stopping');
  const frames = await rec.stop();
  log(`frames=${frames}; encoding`);
  // ffmpeg runs with the frames dir as its only mount, so it writes in place.
  await toGif(`${dir}/frames`, `${name}.gif`, { fps, width: VIEWPORT.width, colors, dither });
  const { rename, stat } = await import('node:fs/promises');
  const finalPath = `${IMAGES_DIR}/${name}.gif`;
  await rename(`${dir}/frames/${name}.gif`, finalPath);
  const { size } = await stat(finalPath);
  log('encoded');
  cdp.close();
  await sweepOldRuns(name);
  return { frames, path: finalPath, mb: +(size / 1048576).toFixed(2) };
}
