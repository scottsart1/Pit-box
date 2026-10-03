/* User-facing grouping and lap-context acceptance on the isolated UDP fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const base = process.env.PITBOX_ENGINEERING_URL;
  const session = process.env.PITBOX_ENGINEERING_SESSION;
  assert.ok(base && session, 'Use engineering-acceptance.py to supply the isolated fixture.');
  const endpoint = `${base}/api/v1/sessions/${encodeURIComponent(session)}/engineering`;
  const output = path.join(process.env.PITBOX_ENGINEERING_EVIDENCE || '.codex-ui-test-data/engineering-ui', 'groups');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PITBOX_BROWSER_CHANNEL ? { channel: process.env.PITBOX_BROWSER_CHANNEL } : {}) });
  const results = [];
  let lastPage = null, currentWidth = null;
  try {
    for (const [width, height] of [[1280, 800], [800, 1280], [390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height }, acceptDownloads: true });
      const read = async () => { const response = await context.request.get(endpoint); assert.equal(response.status(), 200); return response.json(); };
      let fixture = await read();
      assert.ok(fixture.runs.length === 2 && fixture.laps.length === 10, 'Only operate on the isolated two-run synthetic fixture.');
      assert.equal((await context.request.put(`${endpoint}/groups`, { data: { groups: [] } })).status(), 200);
      for (const note of fixture.lap_notes) assert.equal((await context.request.delete(`${endpoint}/lap-notes/${note.id}`)).status(), 200);
      const page = await context.newPage(), errors = [];
      lastPage = page; currentWidth = width;
      const submit = (id, suffix, method) => Promise.all([
        page.waitForResponse(response => response.url() === `${endpoint}${suffix}` && response.request().method() === method),
        page.locator(id).click(),
      ]);
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base);
      const decline = page.getByRole('button', { name: 'No thanks', exact: true });
      if (await decline.isVisible()) await decline.click();
      await page.locator('#onboardingDialog').waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
      if (await page.locator('#onboardingDialog').isVisible()) await page.locator('#onboardingClose').click();
      await page.getByRole('tab', { name: 'ANALYSIS', exact: true }).click();
      await page.getByRole('tab', { name: 'TEST ENGINEER', exact: true }).click();
      await page.locator('#engineeringSession').selectOption(session);
      await page.locator('#engineeringCompare').waitFor({ state: 'visible' });
      await page.locator('#engineeringGroupEditor > summary').click();
      await page.locator('#engineeringSuggestGroups').click();
      await page.waitForFunction(() => document.getElementById('engineeringGroupSelect').options.length === 2);
      assert.equal((await read()).groups.length, 0, 'Suggestions must not save before review.');
      const name = 'Medium baseline <literal text>';
      await page.locator('#engineeringGroupName').fill(name);
      await submit('#engineeringRefresh', '', 'GET');
      assert.equal(await page.locator('#engineeringGroupName').inputValue(), name, 'Refresh preserves group draft.');
      await page.locator('#engineeringSession').selectOption('');
      await page.locator('#engineeringDiscardPrompt').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#engineeringSession').inputValue(), session);
      await page.locator('#engineeringKeepEditing').click();
      assert.equal(await page.locator('#engineeringGroupName').inputValue(), name);
      await page.locator('#engineeringGroupCount').fill('3');
      await page.locator('#engineeringSuggestGroups').click();
      await page.locator('#engineeringReplaceDraft').waitFor({ state: 'visible' });
      await page.locator('#engineeringCancelSuggestion').click();
      assert.equal(await page.locator('#engineeringGroupName').inputValue(), name);
      await page.locator('#engineeringSuggestGroups').click();
      await page.locator('#engineeringConfirmSuggestion').click();
      await page.waitForFunction(() => document.getElementById('engineeringGroupSelect').options.length === 3);
      await page.locator('#engineeringGroupCount').fill('2');
      await page.locator('#engineeringSuggestGroups').click();
      await page.locator('#engineeringConfirmSuggestion').click();
      await page.waitForFunction(() => document.getElementById('engineeringGroupSelect').options.length === 2);
      await page.locator('#engineeringGroupName').fill(name);
      await submit('#engineeringSaveGroups', '/groups', 'PUT');
      await page.getByText('Groups saved. They are ready to compare and included in exports.', { exact: true }).waitFor();
      assert.equal(await page.locator('#engineeringCompareMode').inputValue(), 'stint');
      assert.equal(await page.locator('#engineeringCompareSource').inputValue(), 'groups');
      fixture = await read();
      assert.equal(fixture.groups[0].name, name);
      assert.equal(fixture.groups.length, 2);
      await page.locator('#engineeringCompare').click();
      await page.locator('#engineeringComparison h3').waitFor();
      assert.ok((await page.locator('#engineeringComparison').innerText()).includes('Measured air'));
      assert.ok((await page.locator('#engineeringComparison').innerText()).includes('Reported traffic'));
      // Pull one clean lap into a third, explicitly defined group by ID.
      const moved = fixture.groups[0].laps.find(lap => fixture.groups[0].summary.clean_lap_ids.includes(lap.id));
      await page.locator(`#engineeringLapChoices input[value="${moved.id}"]`).uncheck();
      await page.locator('#engineeringAddGroup').click();
      await page.locator('#engineeringGroupName').fill('Different driving approach');
      await page.locator('#engineeringGroupFrom').selectOption(moved.id);
      await page.locator('#engineeringGroupTo').selectOption(moved.id);
      await page.locator('#engineeringApplyRange').click();
      await submit('#engineeringSaveGroups', '/groups', 'PUT');
      await page.getByText('Groups saved. They are ready to compare and included in exports.', { exact: true }).waitFor();
      fixture = await read();
      assert.equal(fixture.groups.length, 3);
      assert.deepEqual(fixture.groups[2].lap_ids, [moved.id]);
      assert.ok(!fixture.groups[0].lap_ids.includes(moved.id));
      assert.equal(new Set(fixture.groups.flatMap(group => group.lap_ids)).size, fixture.laps.length);
      // Driver-reported traffic stays separate, persists and changes eligible pace evidence.
      await page.locator('#engineeringLapNotes > summary').click();
      const noted = fixture.runs[0].laps.find(lap => fixture.runs[0].summary.clean_lap_ids.includes(lap.id));
      await page.locator('#engineeringNoteFrom').selectOption(noted.id);
      await page.locator('#engineeringNoteTo').selectOption(noted.id);
      const noteText = 'Blocked through sector 2 <driver report>';
      await page.locator('#engineeringLapNoteText').fill(noteText);
      await page.locator('#engineeringNoteExclude').check();
      await submit('#engineeringSaveLapNote', '/lap-notes', 'PATCH');
      await page.getByText("Lap note saved. Comparisons and the engineer's review now include this context.", { exact: true }).waitFor();
      fixture = await read();
      assert.equal(fixture.lap_notes.length, 1);
      assert.equal(fixture.lap_notes[0].text, noteText);
      assert.equal(fixture.lap_notes[0].source, 'driver');
      assert.ok(!fixture.runs[0].summary.clean_lap_ids.includes(noted.id));
      await page.locator('#engineeringCompareSource').selectOption('runs');
      await page.locator('#engineeringCompareMode').selectOption('setup');
      await page.locator('#engineeringCompare').click();
      await page.locator('#engineeringComparison h3').waitFor();
      assert.ok((await page.locator('#engineeringComparison').innerText()).includes(noteText));
      const download = page.waitForEvent('download');
      await page.locator('#engineeringExportText').click();
      const exported = await download, target = path.join(output, `report-${width}.txt`);
      await exported.saveAs(target);
      const exportedText = fs.readFileSync(target, 'utf8');
      assert.ok(exportedText.includes(noteText) && exportedText.includes('Different driving approach'));
      await page.locator('#engineeringContextNotes').getByRole('button', { name: 'Edit note', exact: true }).click();
      await page.locator('#engineeringLapNoteText').fill('Correction: no traffic; I tried a different braking point.');
      await page.locator('#engineeringNoteCategory').selectOption('other');
      await page.locator('#engineeringNoteExclude').uncheck();
      await submit('#engineeringSaveLapNote', '/lap-notes', 'PATCH');
      await page.getByText("Lap note saved. Comparisons and the engineer's review now include this context.", { exact: true }).waitFor();
      fixture = await read();
      assert.equal(fixture.lap_notes.length, 1, 'Editing updates the note instead of appending.');
      assert.ok(fixture.runs[0].summary.clean_lap_ids.includes(noted.id), 'Corrected note restores clean lap.');
      await page.locator('#engineeringCompareSource').selectOption('groups');
      await page.locator('#engineeringCompareMode').selectOption('stint');
      await page.locator('#engineeringCompare').click();
      await page.locator('#engineeringComparison h3').waitFor();
      await page.locator('#engineeringCompareHeading').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `comparison-${width}.png`) });
      await page.locator('#engineeringGroupEditor > summary').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `editor-${width}.png`) });
      await page.locator('#engineeringLapNotes > summary').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `notes-${width}.png`) });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `horizontal overflow at ${width}`);
      await page.locator('#engineeringContextNotes').getByRole('button', { name: 'Remove note', exact: true }).click();
      await page.getByText('Note removed. Any exclusion from this note has also been removed.', { exact: true }).waitFor();
      assert.equal((await read()).lap_notes.length, 0);
      await page.locator('#engineeringRefresh').click();
      await page.waitForFunction(() => document.getElementById('engineeringGroupSelect').options.length === 3);
      if (width === 1280) {
        // Hold completed save responses to verify a later edit cannot be overwritten.
        const holdSave = async (routePath, method) => {
          let release, reached, finished;
          const held = new Promise(resolve => { release = resolve; });
          const ready = new Promise(resolve => { reached = resolve; });
          const completed = new Promise(resolve => { finished = resolve; });
          const handler = async route => {
            if (route.request().method() !== method) return route.continue();
            const response = await route.fetch(); reached(); await held; await route.fulfill({ response }); finished();
          };
          await page.route(routePath, handler);
          return { ready, release: async () => { release(); await completed; await page.unroute(routePath, handler); } };
        };
        await page.locator('#engineeringGroupName').fill('Saved group revision');
        const groupSave = await holdSave(`${endpoint}/groups`, 'PUT');
        await page.locator('#engineeringSaveGroups').click(); await groupSave.ready;
        assert.equal(await page.locator('#engineeringSaveNotes').isDisabled(), true);
        assert.equal(await page.locator('#engineeringSaveLapNote').isDisabled(), true);
        await page.locator('#engineeringGroupName').fill('Newer group draft');
        await groupSave.release();
        await page.getByText('Groups saved. Your newer edits are still unsaved.', { exact: true }).waitFor();
        assert.equal(await page.locator('#engineeringGroupName').inputValue(), 'Newer group draft');
        await submit('#engineeringSaveGroups', '/groups', 'PUT');
        await page.getByText('Groups saved. They are ready to compare and included in exports.', { exact: true }).waitFor();
        await page.locator('#engineeringConclusion').fill('First saved conclusion');
        const planSave = await holdSave(`${endpoint}/notes`, 'PATCH');
        await page.locator('#engineeringSaveNotes').click(); await planSave.ready;
        assert.equal(await page.locator('#engineeringSuggestGroups').isDisabled(), true);
        assert.equal(await page.locator('#engineeringSaveLapNote').isDisabled(), true);
        await page.locator('#engineeringConclusion').fill('Newer conclusion while saving');
        // Force Refresh's session-list request to complete after the plan save.
        // Its normal session-count notice may replace the transient saved notice.
        const refreshList = await holdSave(`${base}/api/v1/sessions?limit=200`, 'GET');
        await page.locator('#engineeringRefresh').click(); await refreshList.ready;
        await planSave.release();
        await page.waitForFunction(() => !document.getElementById('engineeringSaveNotes').disabled);
        assert.equal((await read()).notes.conclusion, 'First saved conclusion');
        const refreshedReport = page.waitForResponse(response => response.url() === endpoint && response.request().method() === 'GET');
        await refreshList.release(); await refreshedReport;
        await page.waitForFunction(() => document.getElementById('engineeringStatus').textContent.includes('automatic runs'));
        assert.equal(await page.locator('#engineeringConclusion').inputValue(), 'Newer conclusion while saving');
        await submit('#engineeringSaveNotes', '/notes', 'PATCH');
        await page.locator('#engineeringLapNoteText').fill('Original lap report');
        const noteSave = await holdSave(`${endpoint}/lap-notes`, 'PATCH');
        await page.locator('#engineeringSaveLapNote').click(); await noteSave.ready;
        await page.locator('#engineeringLapNoteText').fill('Newer lap report while saving');
        await noteSave.release();
        await page.getByText('Lap note saved. Your newer edits are still unsaved.', { exact: true }).waitFor();
        assert.equal(await page.locator('#engineeringLapNoteText').inputValue(), 'Newer lap report while saving');
        await submit('#engineeringSaveLapNote', '/lap-notes', 'PATCH');
        await page.getByText("Lap note saved. Comparisons and the engineer's review now include this context.", { exact: true }).waitFor();
        const saved = await read();
        assert.equal(saved.lap_notes.length, 1);
        assert.equal(saved.lap_notes[0].text, 'Newer lap report while saving');
        assert.equal(saved.notes.conclusion, 'Newer conclusion while saving');
        await page.locator('#engineeringContextNotes').getByRole('button', { name: 'Remove note', exact: true }).click();
        await page.getByText('Note removed. Any exclusion from this note has also been removed.', { exact: true }).waitFor();
      }
      assert.deepEqual(errors, []);
      results.push({ width, height, grouping: 'pass', manualLapSelection: 'pass', draftProtection: 'pass', contextNotes: 'pass', noteCorrection: 'pass', ...(width === 1280 ? { saveRaces: 'pass' } : {}), export: 'pass', overflow: false });
      await context.close();
    }
  } catch (error) {
    if (lastPage && !lastPage.isClosed()) {
      await lastPage.screenshot({ path: path.join(output, `failure-${currentWidth}.png`) }).catch(() => {});
      const state = await lastPage.evaluate(() => ({
        status: document.getElementById('engineeringStatus')?.textContent,
        groupStatus: document.getElementById('engineeringGroupStatus')?.textContent,
        noteStatus: document.getElementById('engineeringLapNoteStatus')?.textContent,
        conclusion: document.getElementById('engineeringConclusion')?.value,
        saveNotesDisabled: document.getElementById('engineeringSaveNotes')?.disabled,
      })).catch(() => ({}));
      fs.writeFileSync(path.join(output, `failure-${currentWidth}.json`), JSON.stringify({ error: String(error), state }, null, 2));
    }
    throw error;
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
