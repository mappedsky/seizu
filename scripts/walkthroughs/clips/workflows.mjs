import { clip } from '../harness.mjs';

// Builds a two-stage workflow whose second stage fans out to two activities
// running in parallel off the first stage's output.
const NAME = 'Critical CVE alerting';
const CYPHER =
  'MATCH (a:GitHubDependabotAlert)-[:FOUND_IN]->(r:GitHubRepository)\n' +
  'WHERE a.severity = "critical"\nRETURN r.name AS repo, count(a) AS alerts';

// Re-recording must not stack up duplicates.
const api = process.env.SEIZU_API || 'http://localhost:8080';
const existing = await (await fetch(`${api}/api/v1/workflows`)).json();
for (const w of existing.workflows ?? existing) {
  if (w.name === NAME) {
    await fetch(`${api}/api/v1/workflows/${w.workflow_id}`, { method: 'DELETE' });
    console.log(`removed a previous "${NAME}"`);
  }
}

const OPTION = { within: '[role="listbox"]' };

const r = await clip('seizu-workflows-walkthrough', { start: '/app/dashboard', speed: 1.7 }, async (c) => {
  await c.click('text=Workflows', { settle: 1800 });
  await c.sleep(1400);                                   // the existing pipelines
  await c.click('text=New Workflow', { settle: 1600 });

  await c.click('label=Name', { settle: 400 });
  await c.type(NAME, { cps: 26 });
  await c.sleep(700);

  // Stage 1: pull the critical alerts out of the graph.
  await c.click('text=Add stage', { settle: 1100 });
  await c.click('text=Add activity', { settle: 1300 });
  await c.click('label=Activity type', { settle: 900 });
  await c.click('text=query', { ...OPTION, settle: 1200 });
  await c.click('label=Output name', { settle: 400 });
  await c.selectAll();
  await c.type('critical_alerts', { cps: 30 });
  await c.click('label=Cypher', { settle: 400 });
  await c.type(CYPHER, { cps: 55 });
  await c.sleep(1100);

  // Stage 2: two activities, both fed by stage 1, running in parallel.
  await c.click('text=Add stage', { settle: 1100 });
  await c.click('text=Add activity', { index: 1, settle: 1300 });
  await c.click('label=Activity type', { index: 1, settle: 900 });
  await c.click('text=slack', { ...OPTION, settle: 1200 });
  await c.click('label=Channels', { settle: 400 });
  await c.type('#security-alerts', { cps: 30 });
  await c.click('label=Title', { settle: 400 });
  await c.type('Critical CVE alerts', { cps: 30 });
  await c.click('label=Initial comment', { settle: 400 });
  await c.type('New critical Dependabot alerts by repository', { cps: 34 });
  await c.click('label=Input', { index: 1, settle: 900 });
  await c.click('text=critical_alerts', { ...OPTION, settle: 1100 });
  await c.sleep(900);

  await c.click('text=Add activity', { index: 1, settle: 1300 });
  await c.click('label=Activity type', { index: 2, settle: 900 });
  await c.click('text=statsd', { ...OPTION, settle: 1200 });
  await c.click('label=Metric name', { settle: 400 });
  await c.type('seizu.critical_alerts', { cps: 30 });
  await c.click('label=Value field', { settle: 400 });
  await c.type('alerts', { cps: 30 });
  await c.click('label=Input', { index: 2, settle: 900 });
  await c.click('text=critical_alerts', { ...OPTION, settle: 1100 });
  await c.sleep(1300);

  await c.click('text=Save workflow', { settle: 3000 });
  await c.waitForText(NAME, 15000);
  await c.sleep(1500);
  // End on the list rather than the detail view: straight after a create, the
  // detail page can sit on its spinner for longer than is worth filming, and
  // the row already shows the trigger and the shape of the pipeline.
  await c.hover(`text=${NAME}`);
  await c.sleep(4000);
});
console.log(JSON.stringify(r));
