/* Run against an isolated app seeded with two synthetic engineering runs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PITBOX_BROWSER_CHANNEL ? { channel: process.env.PITBOX_BROWSER_CHANNEL } : {}) });
  const base = process.env.PITBOX_ENGINEERING_URL || 'http://127.0.0.1:8011';
  const session = process.env.PITBOX_ENGINEERING_SESSION || 'ses_d78018e7227f42d1c376df3e';
  const output = process.env.PITBOX_ENGINEERING_EVIDENCE || '.codex-ui-test-data/engineering-ui';
  fs.mkdirSync(output, { recursive: true });
  const results = [];
  try {
    for (const [width, height] of [[1280, 800], [800, 1280], [390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height }, acceptDownloads: true });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base);
      // Exercise the real optional first-use surfaces, without editing storage.
      const decline = page.getByRole('button', { name: 'No thanks', exact: true });
      if (await decline.isVisible()) await decline.click();
      await page.locator('#onboardingDialog').waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
      if (await page.locator('#onboardingDialog').isVisible()) await page.locator('#onboardingClose').click();
      await page.getByRole('tab', { name: 'SETUP LAB', exact: true }).click();
      await page.getByRole('link', { name: 'Open Test Engineer', exact: true }).click();
      await page.locator('#engineeringSession').selectOption(session);
      await page.locator('#engineeringCompare').click();
      await page.getByRole('heading', { name: 'B was quicker by 0.300 s on the median matched lap.', exact: true }).waitFor();
      assert.equal(await page.locator('.engineering-run').count(), 2);
      const conclusion = 'UI verified conclusion <not HTML>';
      await page.locator('#engineeringConclusion').fill(conclusion);
      await page.locator('#engineeringSaveNotes').click();
      await page.getByText('Test notes saved. They are included in the session report.', { exact: true }).waitFor();
      await page.locator('#engineeringRefresh').click();
      await page.waitForFunction(expected => document.getElementById('engineeringConclusion').value === expected, conclusion);
      const download = page.waitForEvent('download');
      await page.locator('#engineeringExportText').click();
      const file = await download;
      const target = path.join(output, `report-${width}.txt`);
      await file.saveAs(target);
      assert.ok(fs.readFileSync(target, 'utf8').includes(conclusion));
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert.equal(overflow, false, `horizontal overflow at ${width}`);
      await page.locator('#engineeringCompareHeading').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `comparison-${width}.png`) });
      await page.locator('#engineeringTitle').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `runs-${width}.png`) });
      await page.getByRole('tab', { name: 'SESSION REVIEW', exact: true }).click();
      await page.locator('#reviewSessionSelect').selectOption(session);
      await page.locator('#reviewEngineering').click();
      assert.equal(await page.locator('#engineeringSession').inputValue(), session);
      await page.getByRole('tab', { name: 'STRATEGY', exact: true }).click();
      await page.getByRole('heading', { name: 'Who am I racing?', exact: true }).click();
      await page.locator('#strategicRivals').waitFor({ state: 'visible' });
      assert.deepEqual(errors, []);
      results.push({ width, height, comparison: 'pass', notes: 'pass', export: 'pass', navigation: 'pass', overflow: false });
      await context.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
