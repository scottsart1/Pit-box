/* Canonical lap IDs keep user groups and qualitative reports tied to their session. */
const $ = (id) => document.getElementById(id);
let report = null, request = 0, comparisonRequest = 0, active = false;
let notesDirty = false, notesScope = "", selectedSession = "", pendingSession = null, planBusy = false;
let groupDraft = [], groupDirty = false, groupRevision = 0, groupBusy = false;
let lapNoteDirty = false, lapNoteId = null, lapNoteTargets = null, noteBusy = false, noteRevision = 0;
let rivalsPending = false, rivalRequest = 0, lastRivalsAt = 0;
let comparisonReport = null, comparisonReportRequest = 0, comparisonLoading = false;
let comparisonSessions = [], comparisonSessionsRequest = 0, comparisonCursor = null, comparisonTrack = null;
const node = (tag, text = "", cls = "") => {
  const result = document.createElement(tag); result.textContent = String(text);
  if (cls) result.className = cls;
  return result;
};
const value = (number, unit = "") => number == null ? "Unavailable" : `${Number(number).toFixed(3)}${unit}`;
const range = (numbers, unit = "") => Array.isArray(numbers) && numbers.length ? `${numbers.map((n) => Number(n).toFixed(1)).join("–")}${unit}` : "Unavailable";
const status = (text) => { $("engineeringStatus").textContent = text; };
const allLaps = () => report?.laps || (report?.runs || []).flatMap((run) => run.laps);
const lapLabel = (lap) => `Lap ${lap.lap_num}${Number(lap.timeline_epoch) ? ` · timeline ${Number(lap.timeline_epoch) + 1}` : ""}`;
const groupLabel = (group) => group.name || `Run ${group.number} · ${group.compound}`;
const compareItems = () => report?.[$("engineeringCompareSource").value] || [];
const compareSourceB = () => $("engineeringCompareSourceB").value || $("engineeringCompareSource").value;
const reportB = () => $("engineeringSessionB").value ? comparisonReport : report;
const sessionLabel = (session) => `${session.track_name || "Circuit"} · ${session.session_type || "Session"} · ${String(session.started_at || "").slice(0, 19).replace("T", " ") || session.session_id || session.id}`;
const hasDraft = () => notesDirty || groupDirty || lapNoteDirty;
const mutationBusy = () => groupBusy || noteBusy || planBusy;
async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, { ...options, headers: { "Content-Type": "application/json" } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  return body;
}
function fillOptions(id, items, label, includeSession = false) {
  const select = $(id), previous = select.value;
  select.replaceChildren();
  if (includeSession) select.add(new Option("Whole session", ""));
  for (const item of items) select.add(new Option(label(item), item.id));
  if ([...select.options].some((item) => item.value === previous)) select.value = previous;
}
function currentNotes() {
  if (!report) return {};
  return notesScope ? [...report.runs, ...(report.groups || [])].find((run) => run.id === notesScope)?.notes || {} : report.notes;
}
function showNotes() {
  const notes = currentNotes();
  $("engineeringObjective").value = notes.objective || "";
  $("engineeringConclusion").value = notes.conclusion || ""; notesDirty = false;
}
function resetComparison() {
  comparisonRequest++;
  const stint = $("engineeringCompareMode").value === "stint";
  $("engineeringCompare").textContent = stint ? "Compare stints" : "Compare matched laps";
  $("engineeringComparison").replaceChildren(node("p", stint
    ? "Compare the clean laps you selected, including different compounds or setups. Differences in fuel, tyre age, weather and reported context remain visible; this does not isolate what caused a pace change."
    : "The setup test uses the same compound and weather, tyre age within one lap, fuel within 3 kg and track/air temperatures within 3°C. It excludes invalid, pit, flagged, traffic, reported exclusions and incomplete laps.", "muted"));
}
function clearComparisonSession() {
  comparisonReportRequest++; comparisonSessionsRequest++; comparisonReport = null; comparisonLoading = false;
  comparisonSessions = []; comparisonCursor = null; comparisonTrack = null;
  $("engineeringSessionB").replaceChildren(new Option("Same session as A", ""));
  $("engineeringCompareSourceB").value = "";
  $("engineeringSessionBStatus").textContent = ""; $("engineeringMoreSessions").hidden = true;
}
function renderComparisonChoices() {
  const aItems = compareItems(), bItems = reportB()?.[compareSourceB()] || [];
  fillOptions("engineeringRunA", aItems, groupLabel); fillOptions("engineeringRunB", bItems, groupLabel);
  if (reportB()?.session_id === report?.session_id && compareSourceB() === $("engineeringCompareSource").value && bItems.length > 1 && $("engineeringRunA").value === $("engineeringRunB").value) $("engineeringRunB").selectedIndex = 1;
  $("engineeringCompare").disabled = comparisonLoading || !aItems.length || !bItems.length || (reportB()?.session_id === report?.session_id && $("engineeringRunA").value === $("engineeringRunB").value);
  $("engineeringSessionB").disabled = !report;
  $("engineeringRunB").disabled = comparisonLoading || !bItems.length;
  $("engineeringEditSessionB").hidden = !$("engineeringSessionB").value || !comparisonReport;
  $("engineeringSessionALabel").textContent = report ? sessionLabel(report) : "Choose a session above.";
  $("engineeringEditorSession").textContent = report ? `Groups, notes and exports below belong to A: ${sessionLabel(report)}.` : "Choose a session to edit its groups and notes.";
  const preview = $("engineeringSelectionB"); preview.replaceChildren();
  if (comparisonLoading) { preview.append(node("p", "Loading B’s runs and notes…", "muted")); return; }
  if (!bItems.length && reportB()) { preview.append(node("p", compareSourceB() === "groups" ? "No saved lap groups in B. Open that session to define groups, or choose its automatic runs." : "No recorded runs in B.", "muted")); return; }
  const selected = bItems.find((item) => item.id === $("engineeringRunB").value);
  if (!selected || !$("engineeringSessionB").value) return;
  const details = node("details", "", "engineering-evidence");
  details.append(node("summary", `B evidence · ${selected.summary?.clean_lap_count ?? 0} clean laps`), node("p", sessionLabel(reportB()), "small muted"), node("p", `Included: ${(selected.laps || []).map(lapLabel).join(", ")}`));
  const notes = new Map((selected.laps || []).flatMap((lap) => lap.context_notes || []).map((note) => [note.id, note]));
  for (const note of notes.values()) details.append(node("p", `${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
  for (const [label, text] of [["Objective", selected.notes?.objective], ["Conclusion", selected.notes?.conclusion]]) if (text) details.append(node("p", `${label}: ${text}`));
  preview.append(details);
}
async function loadComparisonSessions(more = false) {
  const track = report?.track_id;
  if (!Number.isInteger(track) || track < 0) { $("engineeringSessionBStatus").textContent = "A needs a recorded track identity to find other sessions."; return; }
  const key = report.session_id, ticket = ++comparisonSessionsRequest, cursor = more ? comparisonCursor : null;
  $("engineeringMoreSessions").disabled = true;
  try {
    const result = await api(`/sessions?limit=200&track_id=${track}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    if (ticket !== comparisonSessionsRequest || report?.session_id !== key) return;
    comparisonTrack = track; comparisonCursor = result.next_cursor || null;
    comparisonSessions = [...(more ? comparisonSessions : []), ...(result.items || [])];
    const select = $("engineeringSessionB"), previous = select.value;
    select.replaceChildren(new Option("Same session as A", ""));
    const added = new Set();
    for (const session of comparisonSessions) if (session.id !== key && session.track_id === track && !added.has(session.id)) { select.add(new Option(session.display_name ? `${session.display_name} · ${sessionLabel(session)}` : sessionLabel(session), session.id)); added.add(session.id); }
    if (previous && added.has(previous)) select.value = previous;
    else if (previous && comparisonReport?.session_id === previous && comparisonReport.track_id === track) { select.add(new Option(sessionLabel(comparisonReport), previous)); select.value = previous; }
    $("engineeringSessionBStatus").textContent = added.size ? "Only saved sessions at A’s track are listed. Each session keeps its own notes and lap groups." : "No other saved sessions at this track yet.";
    $("engineeringMoreSessions").hidden = !comparisonCursor;
  } catch (error) { if (ticket === comparisonSessionsRequest) $("engineeringSessionBStatus").textContent = error.message; }
  finally { if (ticket === comparisonSessionsRequest) $("engineeringMoreSessions").disabled = false; }
}
async function loadComparisonReport() {
  const id = $("engineeringSessionB").value, key = report?.session_id, ticket = ++comparisonReportRequest;
  const previousRun = comparisonReport?.session_id === id ? $("engineeringRunB").value : "";
  comparisonReport = null; comparisonLoading = Boolean(id); resetComparison(); renderComparisonChoices();
  if (!id || !report) return;
  try {
    const result = await api(`/sessions/${encodeURIComponent(id)}/engineering`);
    if (ticket !== comparisonReportRequest || report?.session_id !== key || $("engineeringSessionB").value !== id) return;
    if (result.track_id !== report.track_id || !Number.isInteger(result.track_id) || result.track_id < 0) throw new Error("Choose a saved session at the same recorded track as A.");
    comparisonReport = result;
  } catch (error) { if (ticket === comparisonReportRequest) $("engineeringComparison").textContent = error.message; }
  finally { if (ticket === comparisonReportRequest) { comparisonLoading = false; renderComparisonChoices(); if (previousRun && [...$("engineeringRunB").options].some((option) => option.value === previousRun)) { $("engineeringRunB").value = previousRun; renderComparisonChoices(); } } }
}
function conditionLines(conditions) {
  if (!conditions) return [];
  const traffic = conditions.traffic || {};
  const missing = Object.entries(conditions.field_coverage || {}).filter(([, coverage]) => coverage.missing_laps > 0).map(([field, coverage]) => `${field.replaceAll("_", " ")}: ${coverage.observed_laps}/${conditions.lap_count} laps`);
  return [
    `Measured air ${range(conditions.air_temp_c, "°C")} · track ${range(conditions.track_temp_c, "°C")}`,
    `Starting fuel ${range(conditions.fuel_kg, " kg")} · tyre age ${range(conditions.tyre_age_laps, " laps")}`,
    `Compounds: ${(conditions.compounds || []).join(", ") || "Unavailable"} · weather: ${(conditions.weather || []).join(", ") || "Unavailable"}`,
    `Traffic telemetry: ${traffic.measured_affected_laps ?? 0} affected, ${traffic.observed_laps ?? 0} observed, ${traffic.unknown_laps ?? 0} unknown laps. Reported traffic: ${traffic.reported_laps ?? 0} laps.`,
    ...(missing.length ? [`Partial measured coverage — ${missing.join("; ")}. Missing values are not treated as matching conditions.`] : []),
    ...(traffic.caveat ? [traffic.caveat] : []),
  ];
}
function noteLabel(note) { return `${note.source === "engineer" ? "Engineer note" : "Driver report"} · ${(note.category || "other").replaceAll("_", " ")}${note.exclude_from_pace ? " · excluded from pace" : " · context only"}`; }
function renderCards() {
  const items = compareItems(), output = $("engineeringRuns");
  const opened = new Set([...output.querySelectorAll("details[open][data-group]")].map((item) => item.dataset.group));
  output.replaceChildren();
  for (const run of items) {
    const s = run.summary, card = node("details", "", "panel-surface engineering-run");
    card.dataset.group = run.id; card.open = opened.has(run.id);
    const heading = node("summary");
    heading.append(node("strong", groupLabel(run)), node("span", `${s.clean_lap_count} / ${s.lap_count} clean · median ${value(s.median_pace_s, " s")}`, "small muted"));
    card.append(heading);
    const metrics = node("div", "", "summary-grid");
    for (const [label, metric] of [["Median pace", value(s.median_pace_s, " s")], ["Consistency (SD)", value(s.consistency_stdev_s, " s")], ["Observed pace trend", value(s.observed_pace_trend_s_per_lap, " s/lap")], ["Tyre inner temperatures", range(s.tyre_inner_temp_range_c, "°C")]]) {
      const tile = node("div", "", "summary-metric"); tile.append(node("span", label), node("strong", metric)); metrics.append(tile);
    }
    card.append(node("p", `Included: ${(run.laps || []).map(lapLabel).join(", ")}`), metrics, node("p", s.takeaway), node("p", `Next test: ${s.next_test}`), node("p", s.degradation_caveat || "Pace trends include fuel and condition effects.", "small muted"));
    for (const line of conditionLines(run.conditions)) card.append(node("p", line, "small"));
    if (s.balance?.available) card.append(node("p", `Handling indicators: ${s.balance.wheelspin_corner_flags} corner wheelspin flags; ${s.balance.locking_corner_flags} locking flags. ${s.balance.caveat}`, "small"));
    else card.append(node("p", "Balance: insufficient corner evidence.", "small muted"));
    const changes = Object.entries(run.setup_changes || {}).map(([key, change]) => `${key}: ${change.from ?? "unknown"} → ${change.to ?? "unknown"}`);
    card.append(node("p", changes.join("; ") || "No recorded setup change from the previous run.", "small"));
    card.append(node("p", `Setup: ${run.heterogeneous ? "Multiple setups or conditions; review individual lap evidence." : Object.entries(run.setup || {}).map(([key, val]) => `${key} ${val}`).join(", ") || "Unavailable"}`, "small"));
    const notes = new Map();
    for (const lap of run.laps || []) for (const note of lap.context_notes || []) notes.set(note.id, note);
    for (const note of notes.values()) card.append(node("p", `${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
    for (const excluded of s.excluded_laps || []) card.append(node("p", `Lap ${excluded.lap} excluded: ${excluded.reasons.join(", ").replaceAll("_", " ")}`, "small muted"));
    output.append(card);
  }
  if (!items.length) output.append(node("p", $("engineeringCompareSource").value === "groups" ? "No saved lap groups yet. Open ‘Define my lap groups’ to select your laps, then save." : "Complete timed laps to build a run. Returning to the pits produces a debrief; leaving starts the next run.", "empty"));
  renderComparisonChoices();
}
function groupControls() {
  $("engineeringSaveGroups").disabled = !report || !groupDirty || mutationBusy();
  $("engineeringDiscardGroups").disabled = !groupDirty || groupBusy;
  $("engineeringSuggestGroups").disabled = allLaps().length < 2 || mutationBusy();
  $("engineeringAddGroup").disabled = !allLaps().length || groupBusy || groupDraft.length >= 12;
  const assigned = new Set(groupDraft.flatMap((group) => group.lap_ids));
  $("engineeringGroupSummary").textContent = ` ${groupDraft.length} groups${groupDirty ? " · unsaved edits" : " saved"} · ${allLaps().filter((lap) => !assigned.has(lap.id)).length} laps unassigned`;
}
function mutationControls() {
  groupControls();
  $("engineeringSaveNotes").disabled = !report || mutationBusy();
  $("engineeringNotesScope").disabled = mutationBusy();
  $("engineeringSaveLapNote").disabled = !allLaps().length || mutationBusy();
  $("engineeringCancelLapNote").disabled = noteBusy;
}
function markGroupsDirty() { groupDirty = true; groupRevision++; resetComparison(); groupControls(); }
function activeGroup() { return groupDraft.find((group) => group.id === $("engineeringGroupSelect").value); }
function renderGroupEditor() {
  fillOptions("engineeringGroupSelect", groupDraft, (group) => `${group.name} · ${group.lap_ids.length} laps`);
  const group = activeGroup(), laps = allLaps();
  $("engineeringGroupFields").hidden = !group;
  $("engineeringGroupName").value = group?.name || "";
  for (const id of ["engineeringGroupFrom", "engineeringGroupTo"]) fillOptions(id, laps, lapLabel);
  if (group?.lap_ids.length) {
    $("engineeringGroupFrom").value = group.lap_ids[0]; $("engineeringGroupTo").value = group.lap_ids.at(-1);
  }
  const choices = $("engineeringLapChoices"); choices.replaceChildren();
  if (!group) { groupControls(); return; }
  for (const lap of laps) {
    const owner = groupDraft.find((item) => item.id !== group?.id && item.lap_ids.includes(lap.id));
    const label = node("label", "", "engineering-lap-choice");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.value = lap.id;
    checkbox.checked = !!group?.lap_ids.includes(lap.id); checkbox.disabled = !!owner;
    checkbox.setAttribute("aria-label", `${lapLabel(lap)}${owner ? `, assigned to ${owner.name}` : ""}`);
    checkbox.addEventListener("change", () => {
      const selected = new Set(group.lap_ids); if (checkbox.checked) selected.add(lap.id); else selected.delete(lap.id);
      group.lap_ids = laps.filter((item) => selected.has(item.id)).map((item) => item.id); markGroupsDirty();
      const option = [...$("engineeringGroupSelect").options].find((item) => item.value === group.id);
      option.textContent = `${group.name} · ${group.lap_ids.length} laps`;
    });
    const info = node("span"); info.append(node("strong", `${lapLabel(lap)} · ${lap.compound || "Unknown compound"}`));
    info.append(node("span", `${value(Number(lap.lap_time_ms) / 1000, " s")} · air ${lap.air_temp_c ?? "?"}°C · track ${lap.track_temp_c ?? "?"}°C${owner ? ` · in ${owner.name}` : ""}`, "small muted"));
    for (const note of lap.context_notes || []) info.append(node("span", `${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
    label.append(checkbox, info); choices.append(label);
  }
  groupControls();
}
function draftFromReport() { groupDraft = (report?.groups || []).map((group) => ({ id: group.id, name: group.name, lap_ids: [...group.lap_ids] })); groupDirty = false; groupRevision++; renderGroupEditor(); }
function selectedRange(fromId, toId) {
  const laps = allLaps(), from = laps.findIndex((lap) => lap.id === $(fromId).value), to = laps.findIndex((lap) => lap.id === $(toId).value);
  if (from < 0 || to < from) throw new Error("Choose a first lap followed by a last lap in session order.");
  return laps.slice(from, to + 1).map((lap) => lap.id);
}
function applyRange() {
  try {
    const group = activeGroup(), ids = selectedRange("engineeringGroupFrom", "engineeringGroupTo");
    if (!group) return;
    const owner = groupDraft.find((item) => item.id !== group.id && item.lap_ids.some((id) => ids.includes(id)));
    if (owner) throw new Error(`This range includes laps in ${owner.name}. Remove those laps from that group first.`);
    group.lap_ids = ids; markGroupsDirty(); renderGroupEditor(); $("engineeringGroupStatus").textContent = "Range applied to the draft. Save groups to use it in comparisons.";
  } catch (error) { $("engineeringGroupStatus").textContent = error.message; }
}
async function suggestGroups(confirmed = false) {
  if (!report || mutationBusy()) return;
  if (groupDirty && !confirmed) { $("engineeringReplaceDraft").hidden = false; return; }
  const count = Number($("engineeringGroupCount").value);
  if (!Number.isInteger(count) || count < 2 || count > Math.min(12, allLaps().length)) { $("engineeringGroupStatus").textContent = `Choose between 2 and ${Math.min(12, allLaps().length)} groups, with at least one lap each.`; return; }
  const key = report.session_id, revision = groupRevision; groupBusy = true; request++; mutationControls();
  $("engineeringReplaceDraft").hidden = true;
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/groups/suggest`, { method: "POST", body: JSON.stringify({ count }) });
    if (report?.session_id !== key) return;
    if (revision !== groupRevision) { $("engineeringGroupStatus").textContent = "Your edits changed while the suggestion was loading. They have been kept; suggest again when ready."; return; }
    groupDraft = result.groups.map((group) => ({ id: group.id, name: group.name, lap_ids: [...group.lap_ids] }));
    markGroupsDirty(); renderGroupEditor(); $("engineeringGroupStatus").textContent = `${result.explanation || "Groups follow session order."} Review the lap selection, then save.`;
  } catch (error) { if (report?.session_id === key) $("engineeringGroupStatus").textContent = error.message; }
  finally { groupBusy = false; mutationControls(); }
}
async function saveGroups() {
  if (!report || mutationBusy()) return;
  if (notesDirty && notesScope && report.groups?.some((group) => group.id === notesScope) && !groupDraft.some((group) => group.id === notesScope)) { $("engineeringGroupStatus").textContent = "Save the unsaved test notes for the group you are removing before saving this grouping."; return; }
  if (groupDraft.some((group) => !group.name.trim() || !group.lap_ids.length)) { $("engineeringGroupStatus").textContent = "Give every group a name and at least one lap, or remove empty groups."; return; }
  const key = report.session_id, revision = groupRevision; groupBusy = true; request++; mutationControls();
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/groups`, { method: "PUT", body: JSON.stringify({ groups: groupDraft }) });
    if (report?.session_id !== key) return;
    request++; report = result;
    if (revision === groupRevision) draftFromReport();
    $("engineeringCompareSource").value = "groups"; $("engineeringCompareMode").value = "stint";
    resetComparison(); render(); $("engineeringGroupStatus").textContent = groupDirty ? "Groups saved. Your newer edits are still unsaved." : "Groups saved. They are ready to compare and included in exports.";
    status(`${report.runs.length} automatic runs · ${report.groups.length} saved lap groups. Choose your groups or compare the automatic runs.`);
  } catch (error) { if (report?.session_id === key) $("engineeringGroupStatus").textContent = error.message; }
  finally { groupBusy = false; mutationControls(); }
}
function renderLapNotes() {
  const notes = report?.lap_notes || [], output = $("engineeringContextNotes"); output.replaceChildren();
  $("engineeringLapNoteSummary").textContent = ` ${notes.length} saved notes`;
  for (const note of notes) {
    const card = node("article", "", "engineering-context-note");
    const laps = allLaps().filter((lap) => note.lap_ids.includes(lap.id));
    card.append(node("strong", noteLabel(note)), node("p", laps.map(lapLabel).join(", "), "small muted"), node("p", note.text));
    const pending = (note.pending_laps || []).filter((target) => !laps.some((lap) => lap.lap_num === target.lap_num && Number(lap.timeline_epoch || 0) === Number(target.timeline_epoch || 0)));
    if (pending.length) card.append(node("p", `Awaiting recording: ${pending.map(lapLabel).join(", ")}. The note will attach when those laps are saved; you can remove it now or edit after they arrive.`, "small muted"));
    const actions = node("div", "", "linked-actions");
    const edit = node("button", "Edit note", "button ghost"), remove = node("button", "Remove note", "button ghost"); edit.type = remove.type = "button";
    edit.disabled = mutationBusy() || pending.length > 0; remove.disabled = mutationBusy();
    edit.addEventListener("click", () => {
      if (lapNoteDirty) { $("engineeringLapNoteStatus").textContent = "Save or cancel your current note before editing another."; return; }
      lapNoteId = note.id; lapNoteTargets = [...note.lap_ids]; noteRevision++;
      $("engineeringLapNoteHeading").textContent = `Edit note · ${laps.map(lapLabel).join(", ")}`;
      $("engineeringNoteFrom").value = laps[0]?.id || ""; $("engineeringNoteTo").value = laps.at(-1)?.id || "";
      $("engineeringNoteCategory").value = note.category; $("engineeringLapNoteText").value = note.text; $("engineeringNoteExclude").checked = note.exclude_from_pace;
      $("engineeringCancelLapNote").hidden = false; $("engineeringLapNoteText").focus();
    });
    remove.addEventListener("click", () => deleteLapNote(note.id)); actions.append(edit, remove); card.append(actions); output.append(card);
  }
  if (!notes.length) output.append(node("p", "No reported lap context yet.", "muted"));
  for (const id of ["engineeringNoteFrom", "engineeringNoteTo"]) fillOptions(id, allLaps(), lapLabel);
  mutationControls();
}
function clearLapNote() {
  lapNoteDirty = false; lapNoteId = null; lapNoteTargets = null; noteRevision++;
  $("engineeringLapNoteText").value = ""; $("engineeringNoteExclude").checked = false;
  $("engineeringLapNoteHeading").textContent = "Add a lap note"; $("engineeringCancelLapNote").hidden = true;
}
async function saveLapNote() {
  if (!report || mutationBusy()) return;
  const key = report.session_id, revision = noteRevision;
  try {
    const text = $("engineeringLapNoteText").value.trim(); if (!text) throw new Error("Describe what happened on the selected laps.");
    const payload = { lap_ids: lapNoteTargets || selectedRange("engineeringNoteFrom", "engineeringNoteTo"), text, category: $("engineeringNoteCategory").value, exclude_from_pace: $("engineeringNoteExclude").checked };
    if (lapNoteId) payload.note_id = lapNoteId;
    noteBusy = true; request++; mutationControls();
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/lap-notes`, { method: "PATCH", body: JSON.stringify(payload) });
    if (report?.session_id !== key) return;
    request++; report = result;
    if (revision === noteRevision) clearLapNote();
    else if (!lapNoteId) { const saved = result.lap_notes?.find((note) => note.text === payload.text && note.lap_ids.length === payload.lap_ids.length && note.lap_ids.every((id) => payload.lap_ids.includes(id))); if (saved) lapNoteId = saved.id; }
    resetComparison(); render();
    $("engineeringLapNoteStatus").textContent = lapNoteDirty ? "Lap note saved. Your newer edits are still unsaved." : "Lap note saved. Comparisons and the engineer's review now include this context.";
  } catch (error) { if (report?.session_id === key) $("engineeringLapNoteStatus").textContent = error.message; }
  finally { noteBusy = false; renderLapNotes(); }
}
async function deleteLapNote(id) {
  if (!report || mutationBusy()) return;
  const key = report.session_id; noteBusy = true; request++; renderLapNotes();
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/lap-notes/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (report?.session_id !== key) return;
    request++; report = result; if (lapNoteId === id) clearLapNote(); resetComparison(); render();
    $("engineeringLapNoteStatus").textContent = "Note removed. Any exclusion from this note has also been removed.";
  } catch (error) { if (report?.session_id === key) $("engineeringLapNoteStatus").textContent = error.message; }
  finally { noteBusy = false; renderLapNotes(); }
}
function render() {
  $("engineeringTitle").textContent = report ? `${report.track_name} · ${report.session_type}` : "Practice / Test Engineer";
  renderCards(); renderLapNotes();
  fillOptions("engineeringNotesScope", [...(report?.runs || []), ...(report?.groups || [])], groupLabel, true);
  if (notesDirty && notesScope && ![...$("engineeringNotesScope").options].some((option) => option.value === notesScope)) { $("engineeringNotesScope").add(new Option("Previous group · unsaved notes", notesScope)); $("engineeringNotesScope").value = notesScope; }
  if (!notesDirty) { notesScope = $("engineeringNotesScope").value; showNotes(); }
  mutationControls();
  if (!groupDirty) draftFromReport(); else groupControls();
  for (const [id, format] of [["engineeringExportText", "text"], ["engineeringExportJson", "json"]]) {
    $(id).hidden = !report;
    if (report) $(id).href = `/api/v1/sessions/${encodeURIComponent(report.session_id)}/engineering/export?format=${format}`;
    else $(id).removeAttribute("href");
  }
}
async function loadSessions() {
  try {
    const result = await api("/sessions?limit=200"), select = $("engineeringSession");
    const previous = select.value, previousLabel = select.selectedOptions[0]?.textContent;
    select.replaceChildren(new Option("Current live session", ""));
    for (const session of result.items || []) select.add(new Option(session.display_name || `${session.track_name || "Circuit"} · ${session.session_type} · ${(session.started_at || "").slice(0, 16)}`, session.id));
    if (previous && ![...select.options].some((option) => option.value === previous)) select.add(new Option(previousLabel || "Selected session", previous));
    select.value = previous;
  } catch (error) { status(error.message); }
}
async function refresh() {
  if (mutationBusy()) return;
  const ticket = ++request, selected = $("engineeringSession").value;
  try {
    const result = await api(selected ? `/sessions/${encodeURIComponent(selected)}/engineering` : "/engineering/live");
    if (ticket !== request) return;
    const next = result.available === false ? null : result;
    if (next?.session_id !== report?.session_id) {
      if (hasDraft() && report) {
        // Pin the old session rather than applying draft laps or notes to a new track.
        selectedSession = report.session_id;
        const select = $("engineeringSession"); if (![...select.options].some((item) => item.value === selectedSession)) select.add(new Option(`${report.track_name} · unsaved edits`, selectedSession));
        select.value = selectedSession; status("The live session changed. Your edits are kept with the previous session; save them before opening the new session."); return;
      }
      notesDirty = false; groupDirty = false; clearLapNote(); clearComparisonSession(); resetComparison();
    }
    if (report?.session_id === next?.session_id && JSON.stringify([report?.laps, report?.groups, report?.lap_notes]) !== JSON.stringify([next?.laps, next?.groups, next?.lap_notes])) resetComparison();
    report = next; render();
    if (report && comparisonTrack !== report.track_id) loadComparisonSessions();
    status(result.reason || (report ? `${report.runs.length} automatic runs · ${report.groups?.length || 0} saved lap groups. Choose your groups or compare the automatic runs.` : "No saved laps in this session yet."));
  } catch (error) { if (ticket === request) status(error.message); }
}
async function compare() {
  if (!report) return;
  const ticket = ++comparisonRequest, key = report.session_id, bKey = reportB()?.session_id;
  const a = $("engineeringRunA").value, b = $("engineeringRunB").value, source = $("engineeringCompareSource").value, mode = $("engineeringCompareMode").value;
  if (!a || !b || !bKey || comparisonLoading || (a === b && key === bKey)) { $("engineeringComparison").textContent = "Choose two different runs or groups."; return; }
  if ((source === "groups" || (bKey === key && compareSourceB() === "groups")) && groupDirty) { $("engineeringComparison").textContent = "Save or discard your group edits before comparing, so the result uses the lap selection you can see."; return; }
  $("engineeringComparison").textContent = mode === "stint" ? "Reviewing clean laps and recorded context…" : "Matching lap conditions and reviewing context…";
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/compare`, { method: "POST", body: JSON.stringify({ a, b, source, mode, b_session_id: bKey, b_source: compareSourceB() }) });
    if (ticket !== comparisonRequest || report?.session_id !== key || reportB()?.session_id !== bKey) return;
    const output = $("engineeringComparison"); output.replaceChildren(node("h3", result.conclusion), node("p", result.delta_definition));
    for (const side of ["a", "b"]) if (result.selections?.[side]) { const selection = result.selections[side]; output.append(node("p", `${side.toUpperCase()}: ${selection.name} · ${sessionLabel(selection)}`, "small engineering-comparison-origin")); }
    const testNotes = node("details", "", "engineering-evidence"); testNotes.append(node("summary", "Saved test objectives and conclusions"));
    for (const side of ["a", "b"]) for (const [scope, notes] of [["selected run/group", result.selections?.[side]?.notes], ["session", result.selections?.[side]?.session_notes]]) for (const field of ["objective", "conclusion"]) if (notes?.[field]) testNotes.append(node("p", `${side.toUpperCase()} · ${scope} ${field}: ${notes[field]}`));
    if (testNotes.children.length > 1) output.append(testNotes);
    if (result.clean_lap_counts) output.append(node("p", `Clean laps: A ${result.clean_lap_counts.a}, B ${result.clean_lap_counts.b}.`));
    output.append(node("p", `Sector gains/losses: ${(result.sector_deltas_s || []).map((v, i) => `S${i + 1} ${value(v, " s")}`).join(" · ")}`));
    if (result.sector_lap_counts) output.append(node("p", `Complete sector evidence: A ${result.sector_lap_counts.a} laps, B ${result.sector_lap_counts.b} laps.`, "small muted"));
    for (const side of ["a", "b"]) {
      if (result.conditions?.[side]) { output.append(node("h4", `${side.toUpperCase()} · all selected laps`)); for (const line of conditionLines(result.conditions[side])) output.append(node("p", line, "small")); }
      if (result.clean_conditions?.[side]) {
        const cleanEvidence = node("details", "", "engineering-evidence"); cleanEvidence.append(node("summary", `${side.toUpperCase()} · conditions of clean laps used for pace`));
        for (const line of conditionLines(result.clean_conditions[side])) cleanEvidence.append(node("p", line, "small")); output.append(cleanEvidence);
      }
      for (const note of result.notes?.[side] || []) output.append(node("p", `${side.toUpperCase()} · ${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
    }
    if (result.pairs?.length) {
      const evidence = node("details", "", "engineering-evidence"); evidence.append(node("summary", `${result.pairs.length} matched pairs · view lap evidence`));
      for (const pair of result.pairs) evidence.append(node("p", `A lap ${pair.a_lap} / B lap ${pair.b_lap}: ${value(pair.delta_s, " s")} · tyre ages ${pair.tyre_ages.join("/")} · fuel ${pair.fuel_kg.join("/")} kg · track ${pair.track_temp_c.join("/")}°C · air ${pair.air_temp_c?.join("/") || "Unavailable"}°C`, "small"));
      output.append(evidence);
    }
    for (const side of ["a", "b"]) for (const lap of result.excluded?.[side] || []) output.append(node("p", `${side.toUpperCase()} lap ${lap.lap}: ${lap.reasons.join(", ").replaceAll("_", " ")}`, "small muted"));
    if (result.caveats?.length || result.comparison_compatibility) {
      const limits = node("details", "", "engineering-evidence engineering-comparison-limits");
      limits.append(node("summary", result.cross_session ? "Comparison limits · game and car unverified" : "Comparison limits"));
      const compatibility = result.comparison_compatibility;
      if (compatibility) {
        limits.append(node("p", compatibility.track_length_status === "recorded_within_tolerance" ? `Track length: recorded within ${compatibility.track_length_tolerance_m} m.` : "Track length: unavailable for one or both selections.", "small"));
        for (const side of ["a", "b"]) { const selection = result.selections?.[side]; if (selection) limits.append(node("p", `${side.toUpperCase()} · selected track length ${range(selection.track_length_range_m, " m")} · recording format ${selection.packet_format || "Unavailable"}. Recording format does not identify the game version.`, "small")); }
      }
      for (const caveat of result.caveats || []) limits.append(node("p", caveat, "small muted"));
      output.append(limits);
    }
  } catch (error) { if (ticket === comparisonRequest) $("engineeringComparison").textContent = error.message; }
}
async function saveNotes() {
  if (!report || mutationBusy()) return;
  const key = report.session_id, run_id = notesScope || null;
  const objective = $("engineeringObjective").value, conclusion = $("engineeringConclusion").value;
  planBusy = true; request++; mutationControls();
  try {
    const saved = await api(`/sessions/${encodeURIComponent(key)}/engineering/notes`, { method: "PATCH", body: JSON.stringify({ run_id, objective, conclusion }) });
    if (report?.session_id !== key) return;
    request++;
    if (run_id) { const group = [...report.runs, ...(report.groups || [])].find((run) => run.id === run_id); if (group) group.notes = saved; }
    else report.notes = { ...report.notes, ...saved };
    notesDirty = notesScope !== (run_id || "") || $("engineeringObjective").value !== objective || $("engineeringConclusion").value !== conclusion;
    status("Test notes saved. They are included in the session report.");
  } catch (error) { status(error.message); }
  finally { planBusy = false; mutationControls(); }
}
function openSession(id) {
  if (hasDraft() || mutationBusy()) {
    pendingSession = id; $("engineeringSession").value = selectedSession; $("engineeringDiscardPrompt").hidden = false; return;
  }
  selectedSession = id; $("engineeringSession").value = id; request++; report = null; notesScope = ""; notesDirty = false; groupDirty = false; clearLapNote(); clearComparisonSession();
  $("engineeringGroupStatus").textContent = ""; $("engineeringLapNoteStatus").textContent = "";
  resetComparison(); render(); refresh();
}
async function refreshRivals() {
  if (rivalsPending || document.hidden) return;
  rivalsPending = true; const ticket = ++rivalRequest;
  try {
    const result = await api("/engineering/rivals"); if (ticket !== rivalRequest) return;
    const output = $("strategicRivals"); output.replaceChildren();
    if (!result.available) output.append(node("p", result.reason || "Finish projections are still building.", "muted"));
    else {
      for (const rival of result.rivals) output.append(node("p", `${rival.driver} · now P${rival.position} → projected P${rival.projected_position} · ${value(rival.projected_gap_s, " s")} at the finish · ${rival.compound}, age ${rival.tyre_age} · ${rival.estimated_stops_remaining} estimated stops · ${rival.confidence} confidence${rival.gap_assumed ? " (gap assumed)" : ""}`));
      output.append(node("p", "Projected gap is relative to your selected plan; negative means ahead of you. " + result.caveat, "small muted"));
    }
  } catch (error) { $("strategicRivals").textContent = error.message; }
  finally { rivalsPending = false; }
}
$("engineeringSession").addEventListener("change", () => openSession($("engineeringSession").value));
$("engineeringKeepEditing").addEventListener("click", () => { pendingSession = null; $("engineeringDiscardPrompt").hidden = true; });
$("engineeringDiscardAndOpen").addEventListener("click", () => {
  if (mutationBusy()) { status("Wait for the current save to finish before changing sessions."); return; }
  const next = pendingSession; pendingSession = null; $("engineeringDiscardPrompt").hidden = true;
  notesDirty = false; groupDirty = false; clearLapNote(); if (next !== null) openSession(next);
});
$("engineeringRefresh").addEventListener("click", async () => { await loadSessions(); await refresh(); if (report) { await loadComparisonSessions(); if ($("engineeringSessionB").value) await loadComparisonReport(); } });
$("engineeringCompare").addEventListener("click", compare);
for (const id of ["engineeringRunA", "engineeringRunB", "engineeringCompareMode"]) $(id).addEventListener("change", () => { resetComparison(); renderComparisonChoices(); });
$("engineeringCompareSource").addEventListener("change", () => { renderCards(); resetComparison(); });
$("engineeringCompareSourceB").addEventListener("change", () => { resetComparison(); renderComparisonChoices(); });
$("engineeringSessionB").addEventListener("change", loadComparisonReport);
$("engineeringMoreSessions").addEventListener("click", () => loadComparisonSessions(true));
$("engineeringEditSessionB").addEventListener("click", () => { const id = $("engineeringSessionB").value; if (id) { const select = $("engineeringSession"); if (![...select.options].some((option) => option.value === id)) select.add(new Option(sessionLabel(comparisonReport), id)); openSession(id); } });
$("engineeringNotesScope").addEventListener("change", () => {
  if (notesDirty) { $("engineeringNotesScope").value = notesScope; status("Save your test notes before changing their scope."); return; }
  notesScope = $("engineeringNotesScope").value; showNotes();
});
for (const id of ["engineeringObjective", "engineeringConclusion"]) $(id).addEventListener("input", () => { notesDirty = true; });
$("engineeringSaveNotes").addEventListener("click", saveNotes);
$("engineeringSuggestGroups").addEventListener("click", () => suggestGroups());
$("engineeringConfirmSuggestion").addEventListener("click", () => suggestGroups(true));
$("engineeringCancelSuggestion").addEventListener("click", () => { $("engineeringReplaceDraft").hidden = true; });
$("engineeringAddGroup").addEventListener("click", () => {
  const group = { id: `group-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`, name: `Stint ${String.fromCharCode(65 + groupDraft.length)}`, lap_ids: [] };
  groupDraft.push(group); markGroupsDirty(); renderGroupEditor(); $("engineeringGroupSelect").value = group.id; renderGroupEditor(); $("engineeringGroupName").focus();
});
$("engineeringGroupSelect").addEventListener("change", renderGroupEditor);
$("engineeringGroupName").addEventListener("input", () => { const group = activeGroup(); if (group) { group.name = $("engineeringGroupName").value; markGroupsDirty(); const option = $("engineeringGroupSelect").selectedOptions[0]; option.textContent = `${group.name} · ${group.lap_ids.length} laps`; } });
$("engineeringRemoveGroup").addEventListener("click", () => { groupDraft = groupDraft.filter((group) => group.id !== $("engineeringGroupSelect").value); markGroupsDirty(); renderGroupEditor(); });
$("engineeringApplyRange").addEventListener("click", applyRange);
$("engineeringSaveGroups").addEventListener("click", saveGroups);
$("engineeringDiscardGroups").addEventListener("click", () => { draftFromReport(); $("engineeringReplaceDraft").hidden = true; $("engineeringGroupStatus").textContent = "Saved groups restored."; resetComparison(); });
for (const id of ["engineeringNoteFrom", "engineeringNoteTo"]) $(id).addEventListener("change", () => { lapNoteTargets = null; lapNoteDirty = true; noteRevision++; $("engineeringCancelLapNote").hidden = false; });
$("engineeringNoteFrom").addEventListener("change", () => { $("engineeringNoteTo").value = $("engineeringNoteFrom").value; });
for (const id of ["engineeringLapNoteText", "engineeringNoteCategory", "engineeringNoteExclude"]) $(id).addEventListener("input", () => { lapNoteDirty = true; noteRevision++; $("engineeringCancelLapNote").hidden = false; });
$("engineeringSaveLapNote").addEventListener("click", saveLapNote);
$("engineeringCancelLapNote").addEventListener("click", clearLapNote);
window.addEventListener("beforeunload", (event) => { if (hasDraft()) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("pitwall:pagechange", async (event) => {
  active = event.detail?.page === "test-engineer";
  if (active) { await loadSessions(); await refresh(); if ($("engineeringSessionB").value) await loadComparisonReport(); }
  if (event.detail?.page === "strategy") refreshRivals();
});
window.addEventListener("pitwall:engineering-session", (event) => {
  const select = $("engineeringSession"), id = event.detail.sessionId;
  if (![...select.options].some((option) => option.value === id)) select.add(new Option("Selected session", id));
  if (id !== report?.session_id) openSession(id); else { selectedSession = id; select.value = id; }
});
let liveKey = "";
window.addEventListener("pitwall:state", (event) => {
  const s = event.detail, debrief = s.briefings?.post_stint?.text;
  $("stintBriefCard").hidden = !debrief; $("stintBriefText").textContent = debrief || "";
  const key = `${s.session_uid}:${s.restart_epoch}:${s.timeline_epoch}:${s.session_generation}`;
  if (key !== liveKey) {
    liveKey = key; rivalRequest++; $("strategicRivals").textContent = "Finish projections are still building.";
    if (!$("engineeringSession").value) { request++; clearComparisonSession(); resetComparison(); if (!hasDraft() && !mutationBusy()) { report = null; render(); } else renderComparisonChoices(); if (active) refresh(); }
  }
  if (!$("strategy").hidden && Date.now() - lastRivalsAt > 5000) { lastRivalsAt = Date.now(); refreshRivals(); }
});
setInterval(() => { if (active && !document.hidden && !$("engineeringSession").value) refresh(); }, 10000);
resetComparison(); render();
if (location.hash === "#test-engineer" || (!$("test-engineer").hidden && !$("analysis").hidden)) { active = true; loadSessions().then(refresh); }
