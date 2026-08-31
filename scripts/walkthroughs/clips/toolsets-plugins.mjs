import { clip } from '../harness.mjs';

const r = await clip('seizu-toolsets-plugins-walkthrough', { start: '/app/dashboard', speed: 1.2 }, async (c) => {
  await c.click('text=MCP Toolsets', { settle: 2000 });
  // Search rather than paginate: the user-defined toolsets sort after the
  // built-ins, and this shows the list filter at the same time.
  await c.click('[aria-label="Search"]', { settle: 500 });
  await c.type('github', { cps: 12 });
  await c.sleep(1600);
  await c.click('text=GitHub Security', { settle: 2400 });
  await c.sleep(1400);

  // A tool: its Cypher and its declared parameters.
  await c.click('text=top_vulnerabilities', { settle: 2400 });
  await c.sleep(1600);
  await c.scrollBy(380);
  await c.sleep(2400);
  await c.key('Escape');
  await c.sleep(1400);

  // A plugin package: its skills, their declared tools, its files.
  await c.click('text=Agent Plugins', { settle: 2200 });
  await c.sleep(1200);
  await c.click('text=github-security-investigations', { settle: 2400 });
  await c.sleep(1600);
  await c.scrollBy(420);
  await c.sleep(2400);
  await c.scrollBy(420);
  await c.sleep(2400);
  await c.key('Escape');
  await c.sleep(1500);

  // The staged editor the package is authored in.
  await c.click('[aria-label="More actions"]', { index: 1, settle: 1300 });
  await c.click('text=Edit', { within: '[role="menu"]', settle: 3000 });
  await c.sleep(2600);
  await c.scrollBy(420);
  await c.sleep(2600);
  await c.scrollBy(420);
  await c.sleep(2800);
});
console.log(JSON.stringify(r));
