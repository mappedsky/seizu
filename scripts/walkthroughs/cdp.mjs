// Minimal Chrome DevTools Protocol client.
//
// Uses Node's built-in WebSocket (Node 22+), so this whole harness has no
// dependencies and needs nothing installed on the host.
//
// One browser-level connection drives every tab through flat sessions, which
// is why every command takes an optional sessionId rather than opening a
// socket per target.

const HOST = process.env.CDP_HOST || '127.0.0.1:9222';

export async function connect(host = HOST) {
  const version = await (await fetch(`http://${host}/json/version`)).json();
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = () => reject(new Error(`cannot reach Chrome at ${host}`));
  });

  let nextId = 1;
  const pending = new Map();
  const listeners = new Set();

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(`${msg.method ?? ''} ${msg.error.message}`));
      else resolve(msg.result);
    } else if (msg.method) {
      for (const fn of listeners) fn(msg);
    }
  };

  const send = (method, params = {}, sessionId) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });

  const on = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };

  return { send, on, close: () => ws.close() };
}

// Attach to an open tab whose URL contains `url`, or open one.
export async function page(cdp, { url, reuse = true, width = 1568, height = 727, deviceScaleFactor = 1 } = {}) {
  let targetId;
  if (reuse) {
    const { targetInfos } = await cdp.send('Target.getTargets');
    const matches = targetInfos.filter((t) => t.type === 'page' && url && t.url.includes(url));
    // Prefer a tab that has actually loaded: an abandoned about:blank shell at
    // the same URL keeps its entry, and driving that one records a blank page.
    const hit = matches.find((t) => t.title && t.title !== 'about:blank') ?? matches[0];
    if (hit) targetId = hit.targetId;
  }
  if (!targetId) ({ targetId } = await cdp.send('Target.createTarget', { url: url || 'about:blank' }));

  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params) => cdp.send(method, params, sessionId);
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor, mobile: false });
  return { targetId, sessionId, send };
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function goto(p, url, timeout = 15000) {
  await p.send('Page.navigate', { url });
  const deadline = Date.now() + timeout;
  for (;;) {
    const { result } = await p.send('Runtime.evaluate', { expression: 'document.readyState' });
    if (result.value === 'complete' || Date.now() > deadline) return;
    await sleep(100);
  }
}

export async function shot(p, path) {
  const { data } = await p.send('Page.captureScreenshot', { format: 'png' });
  const { writeFile } = await import('node:fs/promises');
  await writeFile(path, Buffer.from(data, 'base64'));
  return path;
}

// The tab must be compositing or the screencast emits nothing at all — see
// record.mjs. Worth checking before a long recording run.
export async function isVisible(p) {
  const { result } = await p.send('Runtime.evaluate', {
    returnByValue: true,
    expression: 'document.visibilityState',
  });
  return result.value === 'visible';
}
