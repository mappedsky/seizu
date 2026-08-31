// Two ways to capture frames, same output shape (frames/*.jpg + a concat list
// carrying real durations, so a pause in the UI stays a pause in the GIF).
//
//  - screencast: Chrome pushes a frame on every compositor update. Smooth, but
//    it emits nothing while the tab is hidden, which includes the whole browser
//    window being on a sleeping display.
//  - polling:    Page.captureScreenshot in a loop. Works regardless of tab
//    visibility, but costs a round trip per frame (~6 fps over the LAN).
import { mkdir, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pad = (i) => String(i).padStart(5, '0');

async function writeFrames(dir, frames, { tailSeconds = 0.8, speed = 1, maxFrameSeconds = 2 } = {}) {
  for (let i = 0; i < frames.length; i++) {
    await writeFile(`${dir}/f${pad(i)}.jpg`, frames[i].buf);
  }
  const lines = [];
  for (let i = 0; i < frames.length; i++) {
    // `speed` compresses real time without dropping frames, so a long build
    // still plays at a watchable length. `maxFrameSeconds` caps a single held
    // frame, which is what actually shortens a clip that spends a minute
    // waiting on a model: it takes the dead air out and leaves the rest.
    const held =
      i < frames.length - 1
        ? Math.max(0.02, frames[i + 1].t - frames[i].t)
        : tailSeconds;
    const dur = Math.min(held, maxFrameSeconds) / speed;
    lines.push(`file 'f${pad(i)}.jpg'`, `duration ${dur.toFixed(3)}`);
  }
  if (frames.length) lines.push(`file 'f${pad(frames.length - 1)}.jpg'`);
  await writeFile(`${dir}/list.txt`, lines.join('\n'));
  return frames.length;
}

export async function record(cdp, p, dir, { probeMs = 1800, pollFps = 8, speed = 1, maxFrameSeconds = 2 } = {}) {
  // A fresh directory per run: re-using one means deleting files ffmpeg wrote
  // through a bind mount, which does not always succeed.
  await mkdir(dir, { recursive: true });

  const frames = [];
  const off = cdp.on((msg) => {
    if (msg.method !== 'Page.screencastFrame' || msg.sessionId !== p.sessionId) return;
    frames.push({ t: msg.params.metadata.timestamp, buf: Buffer.from(msg.params.data, 'base64') });
    p.send('Page.screencastFrameAck', { sessionId: msg.params.sessionId }).catch(() => {});
  });
  await p.send('Page.startScreencast', { format: 'jpeg', quality: 88, everyNthFrame: 1 });

  // A hidden tab never composites, so decide up front which mode this run uses.
  await sleep(probeMs);
  const usingScreencast = frames.length > 0;
  let polling = null;

  if (!usingScreencast) {
    await p.send('Page.stopScreencast').catch(() => {});
    off();
    let stop = false;
    const loop = (async () => {
      const interval = 1000 / pollFps;
      let previous = null;
      while (!stop) {
        const started = Date.now();
        try {
          const { data } = await p.send('Page.captureScreenshot', { format: 'jpeg', quality: 85 });
          const buf = Buffer.from(data, 'base64');
          // An unchanged screen becomes one long frame rather than many copies.
          if (!previous || !previous.equals(buf)) {
            frames.push({ t: Date.now() / 1000, buf });
            previous = buf;
          }
        } catch {
          /* a navigation can drop one frame; keep going */
        }
        const remaining = interval - (Date.now() - started);
        if (remaining > 0) await sleep(remaining);
      }
    })();
    polling = { stop: () => { stop = true; return loop; } };
  }

  return {
    mode: usingScreencast ? 'screencast' : 'polling',
    async stop() {
      if (polling) {
        await polling.stop();
      } else {
        await p.send('Page.stopScreencast').catch(() => {});
        off();
      }
      return writeFrames(dir, frames, { speed, maxFrameSeconds });
    },
  };
}

export async function toGif(dir, out, { fps = 10, width = 1568, colors = 96, dither = 'bayer:bayer_scale=5' } = {}) {
  const vf = `fps=${fps},scale=${width}:-1:flags=lanczos`;
  const run = (args) => exec('docker', [
    'run', '--rm', '-v', `${dir}:/w`, '-w', '/w', '--entrypoint', 'ffmpeg',
    'linuxserver/ffmpeg:latest', ...args,
  ], { maxBuffer: 32 * 1024 * 1024 });
  await run(['-y', '-f', 'concat', '-i', 'list.txt',
    '-vf', `${vf},palettegen=stats_mode=diff:max_colors=${colors}`, 'pal.png']);
  await run(['-y', '-f', 'concat', '-i', 'list.txt', '-i', 'pal.png',
    '-lavfi', `${vf}[x];[x][1:v]paletteuse=dither=${dither}`, out]);
  return `${dir}/${out}`;
}
