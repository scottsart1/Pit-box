/* Real app navigation, persistence and layout checks on an isolated UDP fixture.
   Run through engineering-acceptance.py; never against a user's database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const base = process.env.PITBOX_ENGINEERING_URL;
  const sessionId = process.env.PITBOX_ENGINEERING_SESSION;
  assert.ok(base && sessionId && process.env.PITBOX_ENGINEERING_EVIDENCE);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname));
  const output = path.join(process.env.PITBOX_ENGINEERING_EVIDENCE, 'all-workspaces');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PITBOX_BROWSER_CHANNEL ? { channel: process.env.PITBOX_BROWSER_CHANNEL } : {}) });
  const results = [];
  const persist = () => fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  try {
    for (const [width, height] of [[1280, 800], [800, 1280], [390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      await context.addInitScript(() => {
        window.addEventListener('pitwall:state', event => {
          if (event.detail?.connected && !event.detail?.game_paused) window.__pitboxQaSawActiveTelemetry = true;
        });
      });
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      const run = { width, height, cases: [], screenshots: [], errors: [], serverErrors: [] };
      results.push(run);
      page.on('pageerror', error => run.errors.push({ route: page.url(), message: error.message }));
      page.on('response', response => {
        if (response.url().startsWith(base) && response.status() >= 500) {
          run.serverErrors.push({ url: response.url(), status: response.status() });
        }
      });
      const health = await (await context.request.get(`${base}/api/health`)).json();
      assert.match(health.database || '', /source-qa/, 'Workspace checks require a disposable source-QA database.');
      let delayedFirstPrivacyResponse = false;
      if (width === 1280) await page.route(`${base}/api/v1/usage`, async route => {
        if (route.request().method() !== 'GET' || delayedFirstPrivacyResponse) return route.continue();
        delayedFirstPrivacyResponse = true;
        const response = await route.fetch();
        // Reproduce first-use consent arriving after the old 500ms snapshot.
        // Readiness below waits for UI evidence, not this controlled latency.
        await new Promise(resolve => setTimeout(resolve, 900));
        await route.fulfill({ response });
      });
      const screenshot = async name => {
        const file = `${name}-${width}.png`;
        await page.screenshot({ path: path.join(output, file) });
        run.screenshots.push(file);
      };
      // Retain all discoveries instead of hiding later page failures behind
      // the first defect. The aggregate assertion below still fails CI.
      const check = async (name, action) => {
        try { run.cases.push({ name, status: 'passed', evidence: await action() }); }
        catch (error) {
          run.cases.push({ name, status: 'failed', error: error.message });
          await screenshot(`failure-${name}`);
        }
        persist();
      };
      const settle = async () => {
        await page.waitForFunction(() => document.getElementById('bootOverlay')?.classList.contains('done')
          && document.getElementById('usageToggle')?.disabled === false);
        if (await page.locator('#usagePrompt').isVisible()) {
          await page.locator('#usageNo').click();
          await page.waitForFunction(() => document.getElementById('usagePrompt')?.hidden
            && document.getElementById('usageToggle')?.disabled === false);
        }
        const readiness = await page.waitForFunction(() => {
          if (document.getElementById('onboardingDialog')?.open) return 'visible';
          if (['finished', 'skipped'].includes(localStorage.getItem('pitwall.onboarding.v1'))) return 'remembered';
          if (window.__pitboxQaSawActiveTelemetry) return 'live-session-suppression';
          return false;
        });
        const reason = await readiness.jsonValue();
        if (reason === 'visible') {
          await page.locator('#onboardingClose').click();
          await page.locator('#onboardingDialog').waitFor({ state: 'hidden' });
        }
        return { privacyReady: true, onboarding: reason === 'visible' ? 'dismissed' : reason };
      };
      const open = async id => {
        await page.locator(`#tab-${id}`).click();
        await page.locator(`#${id}`).waitFor({ state: 'visible' });
        await settle();
      };
      await page.goto(base);
      await check('first-use-readiness', async () => ({ ...await settle(), delayedPrivacyResponse: delayedFirstPrivacyResponse }));
      const analysisViews = ['library', 'test-engineer', 'session-review', 'lap-lab', 'field', 'review'];
      for (const id of ['live', 'driver-dashboard', 'strategy', 'connection', 'analysis', 'setup', 'settings', ...analysisViews]) {
        await check(`navigate-${id}`, async () => {
          if (analysisViews.includes(id) && !await page.locator('#analysis').isVisible()) await open('analysis');
          await open(id);
          const tab = await page.locator(`#tab-${id}`).boundingBox();
          assert.ok(tab.height >= 44, `${id} navigation has a 44px touch target`);
          await screenshot(id);
          const box = await page.locator(`#${id}`).evaluate(element => ({
            client: element.clientWidth, scroll: element.scrollWidth,
            top: element.getBoundingClientRect().top, bottom: element.getBoundingClientRect().bottom,
          }));
          assert.ok(box.scroll <= box.client + 1, `${id}: scroll width ${box.scroll} exceeds ${box.client}`);
          return box;
        });
      }
      await check('dashboard-iframe-fits', async () => {
        await open('driver-dashboard');
        const dimensions = await page.locator('#driverDashboardFrame').evaluate(element => ({
          frame: element.getBoundingClientRect().height, available: element.parentElement.clientHeight,
          overflow: getComputedStyle(element.parentElement).overflowY,
        }));
        assert.ok(dimensions.frame <= dimensions.available + 1, JSON.stringify(dimensions));
        return dimensions;
      });
      await check('dashboard-controls-persistence', async () => {
        await open('driver-dashboard');
        const frame = page.frameLocator('#driverDashboardFrame');
        await frame.locator('#sourceSelect').selectOption('live');
        await frame.locator('#customizeButton').click();
        await frame.locator('#unitsSetting').selectOption('mph');
        await frame.locator('#doneSettings').click();
        await frame.locator('#raceModeButton').click();
        await frame.locator('#exitRaceMode').waitFor({ state: 'visible' });
        await frame.locator('#exitRaceMode').click();
        await page.reload();
        await settle();
        await frame.locator('#customizeButton').click();
        assert.equal(await frame.locator('#unitsSetting').inputValue(), 'mph');
        await frame.locator('#unitsSetting').selectOption('kmh');
        await frame.locator('#doneSettings').click();
        return { liveSourceSelectable: true, raceMode: 'entered and exited', unitsPersisted: true };
      });
      await check('browser-back-forward', async () => {
        await open('setup');
        await open('connection');
        await page.goBack();
        assert.equal(await page.locator('#setup').isVisible(), true);
        await page.goForward();
        assert.equal(await page.locator('#connection').isVisible(), true);
      });
      await check('settings-save-reload', async () => {
        await open('settings');
        const control = page.locator('[data-setting="engineer_name"]');
        await control.waitFor();
        const original = await control.inputValue();
        const save = async value => {
          const field = page.locator('[data-setting="engineer_name"]');
          await field.fill(value);
          const response = page.waitForResponse(item => item.url().endsWith('/api/v1/app-settings') && item.request().method() === 'POST');
          await field.press('Tab');
          assert.equal((await response).status(), 200);
        };
        try {
          await save('AuditMark');
          await page.reload();
          await settle();
          await control.waitFor();
          assert.equal(await control.inputValue(), 'AuditMark');
          run.settingsTargets = await page.locator('[data-setting]').evaluateAll(elements => elements.map(element => {
            const target = element.closest('label') || element, box = target.getBoundingClientRect();
            return { name: element.dataset.setting, type: element.type, width: box.width, height: box.height };
          }));
          assert.ok(run.settingsTargets.every(target => target.width >= 44 && target.height >= 44), 'Settings controls need 44px touch targets.');
        } finally { await save(original); }
        return { persisted: true };
      });
      await check('settings-checkbox-touch-save', async () => {
        const checkbox = page.locator('[data-setting="voice_ack_enabled"]');
        const original = await checkbox.isChecked();
        const toggle = async () => {
          const response = page.waitForResponse(item => item.url().endsWith('/api/v1/app-settings') && item.request().method() === 'POST');
          // Click the label padding outside its smaller visible checkbox.
          await checkbox.locator('..').click({ position: { x: 3, y: 3 } });
          assert.equal((await response).status(), 200);
        };
        try {
          await toggle();
          await page.reload();
          await settle();
          await checkbox.waitFor();
          assert.equal(await checkbox.isChecked(), !original);
        } finally { if (await checkbox.isChecked() !== original) await toggle(); }
        return { paddingTogglesControl: true, persistedAfterReload: true };
      });
      await check('recorded-session-review-handoffs', async () => {
        await open('analysis');
        await open('session-review');
        await page.locator(`#reviewSessionSelect option[value="${sessionId}"]`).waitFor({ state: 'attached' });
        await page.locator('#reviewSessionSelect').selectOption(sessionId);
        await page.waitForFunction(() => document.querySelectorAll('#sessionLapRows button').length > 0);
        const laps = await page.locator('#sessionLapRows tr').count();
        assert.ok(laps >= 10);
        await page.locator('#reviewOpenField').click();
        assert.equal(await page.locator('#field').isVisible(), true);
        assert.equal(await page.locator('#fieldSessionSelect').inputValue(), sessionId);
        for (const view of ['classification', 'pace', 'corners', 'positions', 'stints']) {
          assert.ok((await page.locator(`#field-tab-${view}`).boundingBox()).height >= 44);
          await page.locator(`#field-tab-${view}`).click();
          await page.locator(`#field-panel-${view}`).waitFor({ state: 'visible' });
          await settle();
        }
        await page.locator('#fieldStints .stint-card').first().waitFor();
        const counts = await page.locator('#fieldStints .stint-card').evaluateAll(cards => cards.map(card => ({
          summary: card.querySelector('.field-help').textContent, stints: card.querySelectorAll('.stint-row').length,
        })));
        assert.ok(counts.length > 0);
        for (const count of counts) assert.ok(count.summary.startsWith(`${count.stints} stint${count.stints === 1 ? '' : 's'} ·`), JSON.stringify(count));
        await screenshot('recorded-field');
        await open('session-review');
        await page.locator('#reviewEngineering').click();
        assert.equal(await page.locator('#test-engineer').isVisible(), true);
        await page.waitForFunction(id => document.getElementById('engineeringSession').value === id, sessionId);
        return { laps, fieldViews: 5, engineeringHandoff: true };
      });
      await check('library-practice-filter', async () => {
        await open('library');
        await page.locator('#librarySessionType').selectOption('Practice');
        const response = page.waitForResponse(item => item.url().includes('/api/v1/sessions?') && item.url().includes('session_type=Practice'));
        await page.locator('#libraryFilters button').click();
        const data = await (await response).json();
        assert.ok(data.items.some(item => item.id === sessionId), `Practice filter omitted the recorded Practice 1 fixture (${data.items.length} rows).`);
        return { sessions: data.items.length };
      });
      await check('library-reset-review', async () => {
        await page.locator('#librarySessionType').selectOption('');
        await page.locator('#libraryFilters button').click();
        await page.waitForFunction(() => document.querySelectorAll('#libraryRows button').length > 0);
        const actions = await page.locator('#libraryRows button').evaluateAll(buttons => buttons.map(button => ({
          label: button.textContent, height: button.getBoundingClientRect().height,
        })));
        assert.ok(actions.length > 0);
        assert.ok(actions.every(action => action.height >= 44), JSON.stringify(actions));
        return { rows: await page.locator('#libraryRows tr').count() };
      });
      await check('lap-playback-controls', async () => {
        await open('lap-lab');
        await page.locator('#lapLabSessionSelect').selectOption(sessionId);
        await page.waitForFunction(() => document.getElementById('candidateLapSelect').options.length > 1);
        const options = await page.locator('#candidateLapSelect option').evaluateAll(elements => elements.map(element => element.value).filter(Boolean));
        await page.locator('#candidateLapSelect').selectOption(options[0]);
        await settle();
        for (const layer of ['speed', 'throttle', 'brake', 'gear']) {
          assert.ok((await page.locator(`#trace-tab-${layer}`).boundingBox()).height >= 44);
          await page.locator(`#trace-tab-${layer}`).click();
        }
        await page.locator('#playbackToggle').click();
        await settle();
        await screenshot('recorded-lap-lab');
        return { status: await page.locator('#lapLabStatus').innerText(), playback: await page.locator('#playbackToggle').innerText() };
      });
      await check('quit-cancel-preserves-session', async () => {
        await page.locator('#quitApp').click();
        await page.locator('#quitCancel').waitFor({ state: 'visible' });
        await page.locator('#quitCancel').click();
        assert.equal(await page.locator('#quitCancel').isVisible(), false);
        assert.equal((await context.request.get(`${base}/api/health`)).status(), 200);
      });
      await context.close();
      persist();
    }
  } finally { await browser.close(); persist(); }
  const failures = results.flatMap(result => [
    ...result.cases.filter(item => item.status !== 'passed').map(item => ({ width: result.width, ...item })),
    ...result.errors.map(error => ({ width: result.width, ...error })),
    ...result.serverErrors.map(error => ({ width: result.width, ...error })),
  ]);
  console.log(JSON.stringify(results.map(result => ({ width: result.width, cases: result.cases.length,
    failures: result.cases.filter(item => item.status !== 'passed'), pageErrors: result.errors.length,
    serverErrors: result.serverErrors.length })), null, 2));
  assert.deepEqual(failures, [], 'Workspace regression: inspect all-workspaces/results.json and screenshots.');
})().catch(error => { console.error(error); process.exitCode = 1; });
