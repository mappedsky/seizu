import { clip } from '../harness.mjs';

// Deliberately ends without saving: the clip shows the editing surface, and a
// save would add a version to the seeded report every time it is re-recorded.
// Both Cancels are scoped to the dialog — the edit toolbar has one too, and
// clicking that leaves edit mode.
const DIALOG = '[role="dialog"]';

const r = await clip('seizu-edit-report-walkthrough', { start: '/app/dashboard' }, async (c) => {
  await c.click('text=Panel Examples', { settle: 2800 });
  await c.click('text=Edit Report', { settle: 3000 });
  await c.waitForText('Named Queries', 15000);
  await c.sleep(1300);
  await c.wheel(400);                                   // the named Cypher
  await c.sleep(1400);

  await c.click('[aria-label="Edit input"]', { settle: 2400 });
  await c.sleep(1700);
  await c.click('text=Cancel', { within: DIALOG, settle: 1500 });

  await c.click('[aria-label="Edit panel"]', { settle: 2600 });
  await c.sleep(2100);
  await c.click('text=Cancel', { within: DIALOG, settle: 1600 });
  await c.sleep(1100);
});
console.log(JSON.stringify(r));
