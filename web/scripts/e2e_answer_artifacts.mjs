// Live demo check for contract 1.9 artifacts + 1.8 sources: "... as a report" (synthetic demo data only).
import { chromium } from 'playwright';
const OUT = process.argv[2] ?? '/workspace/cc_fe/shots/answer/e5';
const theme = process.argv[3] ?? 'light';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme === 'dark' ? 'dark' : 'light' });
await ctx.addInitScript((t) => { try { localStorage.setItem('cc.pref.theme', t); } catch { /* ignore */ } }, theme);
const pg = await ctx.newPage();
const out = {};
let firstAf = null, n = 0;
await pg.route('**/api/chat', async (route) => {
  n++;
  const res = await route.fetch();
  let body = await res.text();
  if (n === 1) firstAf = /"artifactId":"(af_[^"]+)","version":1,"kind":"chart"/.exec(body)?.[1];
  if (n === 2 && firstAf) {
    // Simulate the server sending v2 of the first chart artifact ("make it a line chart").
    const chartEv = /data: (\{"type":"part","part":\{"type":"artifact"[^\n]*"kind":"chart"[^\n]*)\n/.exec(body);
    if (chartEv) {
      const ev = JSON.parse(chartEv[1]);
      ev.part.artifactId = firstAf; ev.part.version = 2; ev.part.title = 'Paid amount by service month (line)';
      ev.part.content.spec.mark = { type: 'area', line: true, tooltip: true };
      body = body.replace(chartEv[0], `data: ${JSON.stringify(ev)}\n`);
    }
  }
  await route.fulfill({ response: res, body });
});
await pg.goto('http://127.0.0.1:5211/');
const box = pg.getByRole('textbox', { name: 'Message' });
await box.fill('Show paid amount by month as a report'); await pg.keyboard.press('Enter');
await pg.getByTestId('artifact-panel').waitFor({ timeout: 60000 });
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
await pg.waitForTimeout(600);
out.autoOpened = await pg.getByTestId('artifact-panel').getAttribute('aria-label');
out.focusAfterAutoOpen = await pg.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName);
out.cards = await pg.getByTestId('artifact-card').allTextContents();
await pg.screenshot({ path: `${OUT}/${theme}_report_autoopen.png` });
// Sources chip + popover
await pg.getByTestId('sources-chip').first().click();
await pg.getByTestId('sources-popover').waitFor();
out.popover = await pg.getByTestId('sources-popover').textContent();
await pg.screenshot({ path: `${OUT}/${theme}_sources_popover.png` });
await pg.keyboard.press('Escape');
// Chart artifact card
await pg.getByTestId('artifact-card').first().click();
await pg.waitForTimeout(800);
out.chartPanel = await pg.getByTestId('artifact-panel').getAttribute('aria-label');
await pg.screenshot({ path: `${OUT}/${theme}_chart_artifact.png` });
// v2 arrives while v1 is open: the panel follows to v2, chips v1/v2
await box.fill('Paid amount trend by month as a report, as a line'); await pg.keyboard.press('Enter');
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
await pg.waitForTimeout(800);
// autoOpen of the 2nd report must have replaced the panel; re-open the chart card of v1 to see the switch.
await pg.getByTestId('artifact-card').first().click();
await pg.waitForTimeout(500);
out.versionChips = await pg.getByTestId('artifact-versions').textContent().catch(() => null);
out.panelOnV1Card = await pg.getByTestId('artifact-panel').locator('header').textContent();
await pg.screenshot({ path: `${OUT}/${theme}_versions_v1.png` });
await pg.getByTestId('artifact-versions').getByRole('button', { name: 'v2' }).click();
await pg.waitForTimeout(800);
out.panelV2 = await pg.getByTestId('artifact-panel').locator('header').textContent();
await pg.screenshot({ path: `${OUT}/${theme}_versions_v2.png` });
// Typing guard: close the panel, type into the composer while a report streams -> no autoOpen.
await pg.getByRole('button', { name: 'Close panel' }).click();
await box.fill('Paid by payer as a report'); await pg.keyboard.press('Enter');
await box.type('draft follow-up', { delay: 20 });
await pg.getByRole('button', { name: /Send message|Stop/ }).first().waitFor();
await pg.waitForFunction(() => !document.querySelector('[aria-label="Stop generating"], [aria-label="Stop"]'), null, { timeout: 60000 }).catch(() => {});
await pg.waitForTimeout(1500);
out.openedWhileTyping = await pg.getByTestId('artifact-panel').count();
out.composerValue = await box.inputValue();
// Reload: no autoOpen, sources still there
await box.fill('');
await pg.reload();
await pg.getByTestId('msg-assistant').first().waitFor({ timeout: 30000 });
await pg.waitForTimeout(1500);
out.panelAfterReload = await pg.getByTestId('artifact-panel').count();
out.sourcesAfterReload = await pg.getByTestId('sources-chip').count();
await pg.getByTestId('artifact-card').nth(1).click();
await pg.waitForTimeout(500);
await pg.screenshot({ path: `${OUT}/${theme}_report_reload.png` });
// Phone sheet
await pg.setViewportSize({ width: 390, height: 844 });
await pg.waitForTimeout(500);
await pg.screenshot({ path: `${OUT}/${theme}_report_phone.png` });
console.log(JSON.stringify(out, null, 1));
await b.close();
