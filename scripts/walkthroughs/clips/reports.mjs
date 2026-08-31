import { clip } from '../harness.mjs';

// The report's filter bar is sticky, so each input is changed while the panels
// it drives are on screen. Sections are located by their headings rather than
// by offset: filtering changes the report's height.
const r = await clip('seizu-report-walkthrough', { start: '/app/dashboard', speed: 1.15 }, async (c) => {
  await c.click('text=Panel Examples', { settle: 3000 });
  await c.waitForText('About this report', 15000);
  await c.sleep(2400);

  await c.wheelToElement('text=Count panels');
  await c.sleep(2600);
  await c.wheelToElement('text=Progress panels');
  await c.sleep(2600);

  await c.wheelToElement('text=Pie and bar charts');
  await c.sleep(2000);
  await c.click('#panel_examples_search', { settle: 400 });
  await c.selectAll();
  await c.type('CVE-2026', { cps: 7 });                   // the charts re-query as it lands
  await c.sleep(3600);

  await c.wheelToElement('text=Graph panel');
  await c.sleep(1800);
  await c.click('.react-flow__node', { index: 5, settle: 1800 });
  await c.sleep(3000);

  await c.wheelToElement('text=Table with text input filter');
  await c.sleep(3000);

  await c.wheelToElement('text=Table with autocomplete filter');
  await c.sleep(1800);
  await c.click('#panel_examples_severity', { settle: 900 });
  await c.click('text=CRITICAL', { within: '[role="listbox"]', settle: 2600 });
  await c.sleep(3400);
});
console.log(JSON.stringify(r));
