// Re-record every splash-carousel walkthrough. See
// docs/walkthrough-recording-steps.md for what has to be true first.
import { readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const only = process.argv.slice(2);
const clips = (await readdir(join(here, 'clips')))
  .filter((f) => f.endsWith('.mjs'))
  .filter((f) => !only.length || only.includes(f.replace('.mjs', '')));

if (!clips.length) {
  console.error(`no clips matched ${only.join(', ')}`);
  process.exit(1);
}

// Sequentially: they all drive the same browser tab.
for (const file of clips) {
  await import(join(here, 'clips', file));
}
