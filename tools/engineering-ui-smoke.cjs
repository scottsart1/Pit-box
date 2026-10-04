/* Run against an isolated app seeded with two synthetic engineering runs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PITBOX_BROWSER_CHANNEL ? { channel: process.env.PITBOX_BROWSER_CHANNEL } : {}) });
  const base = process.env.PITBOX_ENGINEERING_URL || 'http://127.0.0.1:8011';
  const session = process.env.PITBOX_ENGINEERING_SESSION || 'ses_d78018e7227f42d1c376df3e';
  const crossSession = process.env.PITBOX_ENGINEERING_CROSS_SESSION;
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
      if (crossSession) {
        await page.waitForFunction(id => [...document.getElementById('engineeringSessionB').options].some(option => option.value === id), crossSession);
        const sessionsResponse = await context.request.get(`${base}/api/v1/sessions?limit=200`);
        const sessions = (await sessionsResponse.json()).items;
        const current = sessions.find(item => item.id === session);
        const offered = await page.locator('#engineeringSessionB option').evaluateAll(options => options.map(option => option.value).filter(Boolean));
        assert.ok(offered.includes(crossSession));
        assert.ok(offered.every(id => sessions.find(item => item.id === id)?.track_id === current.track_id), 'B only offers the same recorded track.');
        await page.locator('#engineeringSessionB').selectOption(crossSession);
        await page.locator('#engineeringCompare').click();
        await page.getByRole('heading', { name: 'B was quicker by 0.600 s on the median matched lap.', exact: true }).waitFor();
        assert.equal(await page.locator('#engineeringSession').inputValue(), session);
        assert.ok((await page.locator('#engineeringComparison').innerText()).includes('Practice 2'), 'Result identifies the source session for B.');
        const limits = page.locator('.engineering-comparison-limits');
        assert.equal(await limits.evaluate(element => element.open), false, 'Compatibility explanations stay in optional details.');
        assert.ok((await limits.textContent()).includes('Track length: recorded within 15 m.'));
        assert.ok((await limits.locator('summary').innerText()).includes('game and car unverified'));
        assert.ok((await limits.textContent()).includes('car-performance compatibility were not verified'));
        const values = await page.locator('#engineeringRunB option').evaluateAll(options => options.map(option => option.value));
        assert.ok(values.length > 0);
        await page.locator('#engineeringCompareHeading').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, `cross-session-${width}.png`) });
        await page.locator('#engineeringSessionB').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, `cross-session-selection-${width}.png`) });
        await page.locator('#engineeringComparison h3').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, `cross-session-result-${width}.png`) });
        for (const id of ['engineeringSessionB', 'engineeringCompareSourceB', 'engineeringRunA', 'engineeringRunB', 'engineeringCompare']) assert.ok((await page.locator(`#${id}`).boundingBox()).height >= 44, `${id} has a 44px touch target`);
        await page.getByRole('tab', { name: 'SESSION REVIEW', exact: true }).click();
        const navigationReport = page.waitForResponse(response => response.url() === `${base}/api/v1/sessions/${crossSession}/engineering` && response.request().method() === 'GET');
        await page.getByRole('tab', { name: 'TEST ENGINEER', exact: true }).click();
        await navigationReport;
        await page.waitForFunction(id => !document.getElementById('engineeringCompare').disabled && document.getElementById('engineeringRunB').value === id, values[0]);
        assert.equal(await page.locator('#engineeringSessionB').inputValue(), crossSession, 'B selection survives page navigation.');
        assert.equal(await page.locator('#engineeringRunB').inputValue(), values[0]);
        if (width === 1280) {
          const hold = async (url, method) => {
            let release, reached, finished;
            const held = new Promise(resolve => { release = resolve; });
            const ready = new Promise(resolve => { reached = resolve; });
            const completed = new Promise(resolve => { finished = resolve; });
            await page.route(url, async route => {
              if (route.request().method() !== method) return route.continue();
              const response = await route.fetch(); reached(); await held; await route.fulfill({ response }); finished();
            });
            return { ready, release: async () => { release(); await completed; await page.unroute(url); } };
          };
          // A delayed comparison must not return into a different B selection.
          const pendingCompare = await hold(`${base}/api/v1/sessions/${session}/engineering/compare`, 'POST');
          await page.locator('#engineeringCompare').click(); await pendingCompare.ready;
          await page.locator('#engineeringSessionB').selectOption('');
          await pendingCompare.release();
          assert.equal(await page.locator('#engineeringComparison h3').count(), 0);
          // A delayed B report must not repopulate runs after selecting Same session.
          const pendingReport = await hold(`${base}/api/v1/sessions/${crossSession}/engineering`, 'GET');
          await page.locator('#engineeringSessionB').selectOption(crossSession); await pendingReport.ready;
          assert.equal(await page.locator('#engineeringCompare').isDisabled(), true);
          await page.locator('#engineeringSessionB').selectOption(''); await pendingReport.release();
          assert.equal(await page.locator('#engineeringSessionB').inputValue(), '');
          assert.equal(await page.locator('#engineeringRunB option').count(), 2);
          assert.equal(await page.locator('#engineeringSelectionB').innerText(), '');
          // Switching A to another track clears B and every previous comparison.
          await page.locator('#engineeringSessionB').selectOption(crossSession);
          await page.waitForFunction(() => !document.getElementById('engineeringCompare').disabled);
          const other = sessions.find(item => item.track_id !== current.track_id);
          assert.ok(other, 'Fixture includes the later track transition.');
          await page.locator('#engineeringSession').selectOption(other.id);
          await page.waitForFunction(() => document.getElementById('engineeringTitle').textContent.includes('Monza'));
          assert.equal(await page.locator('#engineeringSessionB').inputValue(), '');
          assert.equal(await page.locator('#engineeringCompare').isDisabled(), true);
          assert.equal(await page.locator('#engineeringComparison h3').count(), 0);
          await page.locator('#engineeringSession').selectOption(session);
        } else await page.locator('#engineeringSessionB').selectOption('');
        await page.locator('#engineeringCompare').click();
        await page.getByRole('heading', { name: 'B was quicker by 0.300 s on the median matched lap.', exact: true }).waitFor();
      }
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
      results.push({ width, height, comparison: 'pass', cross_session: crossSession ? 'pass' : 'not supplied', cross_session_races: crossSession && width === 1280 ? 'pass' : 'not run', notes: 'pass', export: 'pass', navigation: 'pass', overflow: false });
      await context.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
