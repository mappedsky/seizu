import { clip } from '../harness.mjs';

const api = process.env.SEIZU_API || 'http://localhost:8080';

// The history panel shows the most recent queries, so seed it with ones worth
// reading. The last one seeded is the one the clip recalls.
const SEED = [
  'MATCH (a:GitHubDependabotAlert) RETURN a.severity AS severity, count(*) AS alerts ORDER BY alerts DESC',
  'MATCH (r:GitHubRepository)<-[:FOUND_IN]-(a:GitHubDependabotAlert) RETURN r.name AS repo, count(a) AS alerts ORDER BY alerts DESC',
  'MATCH path = (r:GitHubRepository)<-[:FOUND_IN]-(a:GitHubDependabotAlert)\nWHERE a.severity = "critical"\nRETURN path LIMIT 15',
];
for (const query of SEED) {
  await fetch(`${api}/api/v1/query/adhoc`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
}

const RECALLED = 'MATCH path = (r:GitHubRepository)<-[:FOUND_IN]-(a:GitHubDependabotAlert)';

const r = await clip('seizu-query-console-walkthrough', { start: '/app/dashboard', speed: 1.25 }, async (c) => {
  await c.click('text=Query Console', { settle: 2400 });
  await c.hover('text=GitHubDependabotAlert');
  await c.sleep(1300);                                    // schema tooltip
  await c.click('text=GitHubDependabotAlert', { settle: 2800 });
  await c.waitForText('Table', 20000);
  await c.sleep(1400);

  // Select a node: the sidebar fills with its properties.
  await c.click('.react-flow__node', { index: 6, settle: 2000 });
  await c.sleep(2600);
  await c.wheel(320, { x: 1430, y: 400 });                // through its properties
  await c.sleep(2200);

  await c.click('text=Table', { settle: 1800 });
  await c.sleep(1400);
  await c.click('text=Raw', { settle: 1600 });
  await c.sleep(1400);
  await c.click('text=Graph', { settle: 1300 });

  // Recall a query from history rather than retyping it.
  await c.click('[aria-label="Query history"]', { settle: 1800 });
  await c.sleep(1500);
  await c.click(`has=.MuiListItemButton-root::${RECALLED}`, { settle: 1800 });
  await c.sleep(1300);
  await c.click('text=Run', { settle: 3000 });
  await c.sleep(2600);
  await c.click('.react-flow__node', { index: 3, settle: 1800 });
  await c.sleep(2600);
});
console.log(JSON.stringify(r));
