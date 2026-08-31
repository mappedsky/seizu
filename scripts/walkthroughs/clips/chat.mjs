import { clip } from '../harness.mjs';

// A real turn against the configured model, followed through to its answer.
// A turn runs a minute or two, most of it spent waiting on the model, so the
// clip leans on maxFrameSeconds to drop the dead air rather than on speed.
const QUESTION = 'Which repositories have the most critical CVEs?';

const r = await clip(
  'seizu-chat-walkthrough',
  // A turn is mostly waiting, and what does change is streaming text, which
  // is the most expensive thing a GIF can encode. Hence the aggressive dead-air
  // cap, the lower frame rate and the smaller palette: this clip is otherwise
  // several times the size of every other one.
  { start: '/app/dashboard', speed: 2.8, maxFrameSeconds: 0.45, fps: 6, colors: 64 },
  async (c) => {
    await c.click('text=Chat', { settle: 2400 });
    await c.click('textarea, input[placeholder*="Ask"]', { settle: 500 });
    await c.type(QUESTION, { cps: 21 });
    await c.sleep(700);
    await c.key('Enter');
    await c.sleep(4000);

    // Follow the answer while it streams: routing, thinking, tool calls.
    for (let i = 0; i < 14; i++) {
      const stillWorking = await c.waitForText('is working', 1500);
      if (!stillWorking) break;
      await c.wheel(300, { x: 900, y: 470, steps: 8 });
      await c.sleep(2500);
    }
    await c.waitForGone('is working', 150000);

    // Let the finished answer stand before the loop restarts.
    await c.sleep(1500);
    await c.wheel(500, { x: 900, y: 470, steps: 12 });
    await c.sleep(2000);
    await c.wheel(500, { x: 900, y: 470, steps: 12 });
    await c.sleep(5000);
  },
);
console.log(JSON.stringify(r));
