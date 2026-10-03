/* Source-backed setup acceptance. Only run against an isolated engineering fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const base = process.env.PITBOX_ENGINEERING_URL;
  assert.ok(base, 'Use engineering-acceptance.py to supply an isolated app URL.');
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'QA must use a loopback app.');
  const output = path.join(process.env.PITBOX_ENGINEERING_EVIDENCE || '.codex-ui-test-data/engineering-ui', 'setup-references');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PITBOX_BROWSER_CHANNEL ? { channel: process.env.PITBOX_BROWSER_CHANNEL } : {}) });
  const results = [];
  let lastPage = null, currentWidth = null;
  try {
    for (const [width, height] of [[1280, 800], [800, 1280], [390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      const healthResponse = await context.request.get(`${base}/api/health`);
      assert.equal(healthResponse.status(), 200);
      const health = await healthResponse.json();
      assert.ok(/source-qa|com\.yourpitbox\.app\.(qa|debug)/.test(health.database || ''), 'Refusing setup mutations in production.');
      const page = await context.newPage(), errors = [];
      lastPage = page; currentWidth = width;
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base);
      const decline = page.getByRole('button', { name: 'No thanks', exact: true });
      if (await decline.isVisible()) await decline.click();
      await page.locator('#onboardingDialog').waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
      if (await page.locator('#onboardingDialog').isVisible()) await page.locator('#onboardingClose').click();
      await page.getByRole('tab', { name: 'SETUP LAB', exact: true }).click();
      await page.locator('#setupTrack option[value="0"]').waitFor({ state: 'attached' });
      await page.locator('#setupTrack').selectOption('0');
      await page.locator('#setupBasis').selectOption('reference');
      await page.locator('#setupConditions').selectOption('dry');
      await page.locator('#setupReferenceStyle').selectOption('stable');
      assert.equal(await page.locator('#setupChangeLevel').isDisabled(), true);

      const generate = async (profile, expectedStatus = 200) => {
        const [response] = await Promise.all([
          page.waitForResponse(r => r.url() === `${base}/api/setup/recommend` && r.request().method() === 'POST'),
          page.locator(`.setupProfile[data-profile="${profile}"]`).click(),
        ]);
        assert.equal(response.status(), expectedStatus, await response.text());
        const data = await response.json();
        await page.waitForFunction(() => !document.querySelector('.setupProfile').disabled);
        if (expectedStatus === 200) {
          assert.equal(data.basis, 'reference');
          assert.equal(data.track_id, 0);
          assert.equal(data.profile, profile);
          assert.ok(data.baseline_reference.sources.some(source => source.url.startsWith('https://')));
          assert.ok(await page.locator('#setupReference a[href^="https://"]').count());
          const displayed = await page.locator('#recommendedSetup .row').evaluateAll(rows => Object.fromEntries(rows.map(row => [row.querySelector('span').textContent, row.querySelector('b').textContent])));
          for (const [field, value] of Object.entries(data.recommended)) {
            assert.equal(Number(displayed[field.replaceAll('_', ' ')]), Number(value), `Displayed ${field} must match API.`);
          }
          assert.equal(data.setup_effects.calibrated, false);
          assert.equal(data.pit_adjustment.available, false, 'Garage reference is not a live wing instruction.');
        } else {
          assert.equal(await page.locator('#recommendedSetup .row').count(), 0, 'Unavailable reference must clear previous settings.');
          assert.equal(await page.locator('#setupChanges').innerText(), '');
          assert.equal(await page.locator('#setupPit').innerText(), '');
          assert.equal(await page.locator('#setupReference').isVisible(), false);
          assert.match(await page.locator('#setupRationale').innerText(), /wet|reference|available/i);
        }
        return data;
      };

      const stable = await generate('race');
      assert.equal(stable.recommended.front_wing, 21);
      assert.equal(stable.recommended.rear_wing, 17);
      await page.locator('#setupTitle').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `stable-${width}.png`) });
      await page.locator('#setupReferenceStyle').selectOption('rotation');
      const race = await generate('race');
      assert.equal(race.recommended.front_wing, 42);
      assert.equal(race.recommended.rear_wing, 15);
      assert.equal(race.recommended.off_throttle, 60);
      const quali = await generate('quali');
      assert.equal(quali.recommended.front_wing, 30);
      assert.equal(quali.recommended.rear_wing, 0);
      assert.equal(quali.recommended.off_throttle, 45);
      assert.notDeepEqual(quali.recommended, race.recommended, 'Source race/quali differences must survive the API.');
      assert.match(JSON.stringify(quali.baseline_reference), /parc.?ferm/i);
      await page.locator('#setupTitle').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `rotation-quali-${width}.png`) });

      // Reopening Setup Lab must not silently switch back to the live circuit.
      await page.getByRole('tab', { name: 'DRIVE', exact: true }).click();
      const navigationTracks = page.waitForResponse(r => r.url() === `${base}/api/tracks`);
      await page.getByRole('tab', { name: 'SETUP LAB', exact: true }).click();
      await (await navigationTracks).finished();
      assert.equal(await page.locator('#setupTrack').inputValue(), '0');
      assert.equal(await page.locator('#setupReferenceStyle').inputValue(), 'rotation');

      // A delayed navigation fetch must preserve a choice made while it was pending.
      let releaseTracks, sawTracks;
      const heldTracks = new Promise(resolve => { releaseTracks = resolve; });
      const interceptedTracks = new Promise(resolve => { sawTracks = resolve; });
      const trackRoute = async route => {
        const response = await route.fetch();
        sawTracks();
        await heldTracks;
        await route.fulfill({ response });
      };
      await page.route('**/api/tracks', trackRoute);
      await page.getByRole('tab', { name: 'DRIVE', exact: true }).click();
      await page.getByRole('tab', { name: 'SETUP LAB', exact: true }).click();
      await interceptedTracks;
      await page.locator('#setupTrack').selectOption('2');
      const resumedTracks = page.waitForResponse(r => r.url() === `${base}/api/tracks`);
      releaseTracks();
      await (await resumedTracks).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator('#setupTrack').inputValue(), '2', 'A late circuit-list response must preserve the latest user selection.');
      await page.unroute('**/api/tracks', trackRoute);
      await page.locator('#setupTrack').selectOption('0');
      await page.locator('#setupBasis').selectOption('personalized');
      assert.equal(await page.locator('#setupChangeLevel').isDisabled(), false);
      await page.locator('#setupChangeLevel').selectOption('radical');
      await page.locator('#setupBasis').selectOption('reference');
      assert.equal(await page.locator('#setupChangeLevel').isDisabled(), true);

      await page.locator('#setupConditions').selectOption('wet');
      await generate('race', 409);
      await page.screenshot({ path: path.join(output, `wet-unavailable-${width}.png`) });
      await page.locator('#setupConditions').selectOption('dry');
      const fresh = await generate('race');
      assert.deepEqual(fresh.recommended, race.recommended, 'Fresh reference cannot inherit another profile or personalization scope.');
      for (const invalid of [{ basis: 'invented' }, { reference_style: 'invented' }, { conditions: 'snow' }]) {
        const response = await context.request.post(`${base}/api/setup/recommend`, { data: { track_id: 0, basis: 'reference', ...invalid } });
        assert.equal(response.status(), 422, 'Invalid selector must fail API validation.');
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `Horizontal overflow at ${width}.`);
      assert.deepEqual(errors, []);
      results.push({ width, height, stable: 'pass', rotation_race_quali: 'pass', source_links: 'pass', exact_display_values: 'pass', unsupported_wet_clears: 'pass', selection_navigation: 'pass', delayed_selection_preserved: 'pass', personal_controls: 'pass', invalid_api_selectors: 'pass', overflow: false });
      await context.close(); lastPage = null;
    }
  } catch (error) {
    if (lastPage && !lastPage.isClosed()) await lastPage.screenshot({ path: path.join(output, `failure-${currentWidth}.png`) }).catch(() => {});
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ width: currentWidth, error: String(error), completed: results }, null, 2));
    throw error;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
