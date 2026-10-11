const API_BASE = "/api/v1/transfers";
const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";
const TERMINAL = new Set(["completed", "failed", "conflict"]);
const state = { active: false, timer: null, refreshing: false, busy: false, stale: false, status: null, peer: null, sessions: [], selected: new Set(), catalogRequest: 0, peerSignature: "", jobSignature: "", announcedJobs: new Set() };
const byId = (id) => HAS_DOM ? document.getElementById(id) : null;

export function transferError(payload, fallback = i18n.t("The request could not be completed.")) {
  const detail = payload?.detail ?? payload;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail?.message === "string") return detail.message;
  return fallback;
}

export function formatTransferBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return i18n.t("Size unavailable");
  if (size < 1024) return `${Math.round(size)} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${(size / 1024 ** 3).toFixed(2)} GB`;
}

export function transferJobLabel(value) {
  return { queued: i18n.t("Waiting to start"), preparing: i18n.t("Preparing history"), downloading: i18n.t("Copying history"), verifying: i18n.t("Checking received history"), importing: i18n.t("Adding sessions to this device"), completed: i18n.t("History copied"), failed: i18n.t("Transfer needs attention"), conflict: i18n.t("History needs review") }[value] || i18n.t("Checking transfer");
}

export function transferStage(job, outgoing = false) {
  const phases = { snapshot: i18n.t("Copying database snapshot"), records: i18n.t("Gathering history records"), packing: i18n.t("Packing file entries"), finalizing: i18n.t("Finalizing archive"), checksum: i18n.t("Checking archive checksum"), extracting: i18n.t("Extracting and checking files"), checking_rows: i18n.t("Checking history records"), checking_links: i18n.t("Checking history relationships"), importing_rows: i18n.t("Merging history records"), saving_files: i18n.t("Saving detail files"), committing: i18n.t("Committing history safely") };
  const copying = outgoing ? ["sending", "sent", "interrupted"].includes(job.status) : job.status === "downloading";
  const event = copying ? { done: outgoing ? job.bytes_sent : job.bytes_received, total: job.total_bytes, unit: "bytes" } : job.progress || {};
  const label = copying ? (outgoing ? i18n.t("Sending archive") : i18n.t("Receiving archive")) : phases[event.phase] || (job.status === "preparing" ? (outgoing ? i18n.t("Preparing export") : i18n.t("Preparing on the sending device")) : transferJobLabel(job.status));
  const unit = ["bytes", "files", "rows", "pages"].includes(event.unit) ? event.unit : null;
  const done = unit && Number.isSafeInteger(event.done) && event.done >= 0 ? event.done : null;
  const total = done !== null && Number.isSafeInteger(event.total) && event.total > 0 && event.total >= done ? event.total : null;
  const count = value => unit === "bytes" ? formatTransferBytes(value) : value.toLocaleString();
  const amounts = done === null ? null : { done: count(done), total: total === null ? null : count(total) };
  const detail = done === null ? i18n.t("Working — total not available yet")
    : total === null ? (unit === "bytes" ? i18n.t("{done} processed · total not available yet", amounts) : unit === "files" ? i18n.t("{done} files processed · total not available yet", amounts) : unit === "rows" ? i18n.t("{done} rows processed · total not available yet", amounts) : i18n.t("{done} pages processed · total not available yet", amounts))
    : unit === "bytes" ? i18n.t("{done} of {total}", amounts) : unit === "files" ? i18n.t("{done} of {total} file entries", amounts) : unit === "rows" ? i18n.t("{done} of {total} rows", amounts) : i18n.t("{done} of {total} pages", amounts);
  return { label, done, total, detail };
}

export function sessionIdentity(session) { return String(session?.session_id ?? session?.id ?? ""); }

function completenessLevel(session) {
  const detail = session?.completeness ?? session?.telemetry_completeness ?? session?.detail_level;
  return typeof detail === "object" ? detail?.level ?? detail?.status : detail;
}
const SUMMARY_ONLY = ["summary", "summary_only", "none"];

export function sessionCompleteness(session) {
  const value = completenessLevel(session);
  if (["full", "complete", "full_telemetry"].includes(value)) return { label: i18n.t("Full telemetry"), tone: "healthy" };
  if (["partial", "some", "partial_telemetry"].includes(value)) return { label: i18n.t("Partial telemetry"), tone: "warning" };
  if (SUMMARY_ONLY.includes(value)) return { label: i18n.t("Summary only"), tone: "warning" };
  if (value === "cataloged-detail") return { label: i18n.t("Lap detail recorded"), tone: "neutral" };
  return { label: i18n.t("Detail not reported"), tone: "neutral" };
}

export function selectedSessionIds(sessions, selected) {
  return [...new Set(sessions.filter((session) => sessionIdentity(session) && session.transferable !== false && selected.has(sessionIdentity(session))).map(sessionIdentity))];
}

function textElement(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = String(value ?? "");
  return element;
}
function text(id, value) { const el = byId(id); if (el) el.textContent = String(value ?? ""); }
function notice(message, tone = "info") { text("transferStatus", message); if (byId("transferStatus")) byId("transferStatus").dataset.tone = tone; }
function action(label, handler, className = "button ghost") {
  const button = textElement("button", className, label); button.type = "button"; button.addEventListener("click", handler); return button;
}
function dateLabel(value) {
  if (!value) return i18n.t("Date unavailable");
  const parsed = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(parsed.getTime()) ? i18n.t("Date unavailable") : parsed.toLocaleString();
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options, credentials: "same-origin", cache: "no-store", signal: controller.signal,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error(transferError(payload, response.status === 404 ? i18n.t("Wi-Fi transfers are unavailable in this build. Update both devices to a compatible release.") : i18n.t("Transfer service returned {status}.", { status: response.status })));
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(i18n.t("The request timed out. Check both apps are open on the same network, then refresh before trying again."));
    if (error instanceof TypeError) throw new Error(i18n.t("Cannot reach this device’s transfer service. Check Pit Wall is still running."));
    throw error;
  } finally { clearTimeout(timeout); }
}

function activeJobs() { return (state.status?.jobs || []).some((job) => !TERMINAL.has(job.status)); }
function activeTransfers() { return activeJobs() || (state.status?.exports || []).some((job) => ["preparing", "sending"].includes(job.status)); }
function setBusy(value) {
  state.busy = value;
  for (const id of ["transferEnable", "transferDisable", "transferInvite", "transferPair", "transferRefresh"]) {
    if (byId(id)) byId(id).disabled = value;
  }
  byId("transferDeviceName").disabled = value || Boolean(state.status?.running);
  byId("transferDisable").disabled = value;
  byId("transferDisable").title = activeJobs() ? i18n.t("Pause copying; an import already committing will finish safely.") : i18n.t("Turn off Wi-Fi transfers");
  byId("transferPeers").querySelectorAll("button").forEach((button) => { button.disabled = value; });
  byId("transferJobs").querySelectorAll("button").forEach((button) => { button.disabled = value; });
  updateSelection();
}

async function mutate(message, operation) {
  if (state.busy) return;
  setBusy(true); notice(message);
  try { await operation(); } catch (error) { notice(error.message, "error"); }
  finally { setBusy(false); schedule(); }
}

function renderStatus(payload) {
  state.status = payload;
  state.stale = false;
  const running = Boolean(payload?.running);
  byId("transferBadge").dataset.state = running ? "healthy" : "neutral";
  text("transferBadge", running ? i18n.t("Wi-Fi transfers on") : i18n.t("Off"));
  byId("transferEnable").hidden = running;
  byId("transferDisable").hidden = !running;
  byId("transferWorkspace").hidden = !running;
  const name = byId("transferDeviceName");
  if (document.activeElement !== name && payload.device_name) name.value = payload.device_name;
  name.disabled = running || state.busy;
  text("transferEndpoint", running ? (payload.endpoint ? i18n.t("Available on your home network · {endpoint}. Keep both apps open during transfers.", { endpoint: `${payload.endpoint}` }) : i18n.t("Available on your home network. Keep both apps open during transfers.")) : i18n.t("Enable transfers on both devices. Turning transfers off does not remove your saved history."));
  if (!running) { byId("transferInvitation").value = ""; byId("transferInvitationPanel").hidden = true; byId("transferInvitationQr").removeAttribute("src"); }
  renderPeers(payload.peers || []);
  renderJobs(payload.jobs || [], payload.exports || []);
  setBusy(state.busy);
}

function renderPeers(peers) {
  const signature = JSON.stringify(peers);
  if (signature === state.peerSignature) return;
  state.peerSignature = signature;
  const host = byId("transferPeers"); host.replaceChildren();
  if (!peers.length) { host.append(textElement("p", "muted", i18n.t("No paired devices yet. Create or paste an invitation above."))); return; }
  for (const peer of peers) {
    const card = textElement("article", "transfer-peer", "");
    card.append(textElement("strong", "", peer.name || i18n.t("Paired device")), textElement("div", "transfer-peer-address", peer.endpoint || ""));
    const actions = textElement("div", "transfer-actions", "");
    actions.append(action(i18n.t("Browse sessions"), () => browse(peer)), action(i18n.t("Remove pairing"), () => revoke(peer)));
    card.append(actions); host.append(card);
  }
}

async function refresh({ announce = false } = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const wasStale = state.stale;
    const payload = await api("/status"); renderStatus(payload);
    if (payload.error) notice(payload.error, "error");
    else if (announce || wasStale) notice(payload.running ?
      (payload.jobs?.length || payload.exports?.length ? i18n.t("Transfer status is shown below. Check the receiving device for the final import result.") : i18n.t("Ready. Pair another device or browse an existing pairing below.")) :
      i18n.t("Wi-Fi transfers are off. Give this device a name, then enable transfers."));
  } catch (error) {
    notice(i18n.t("{error} Progress below is the last known state.", { error: `${error.message}` }), "error");
    text("transferBadge", i18n.t("Unavailable")); byId("transferBadge").dataset.state = "error";
    state.stale = true; renderJobs(state.status?.jobs || [], state.status?.exports || []);
  }
  finally { state.refreshing = false; }
}

async function enable(event) {
  event.preventDefault();
  await mutate(i18n.t("Starting encrypted Wi-Fi transfers…"), async () => {
    const name = byId("transferDeviceName").value.trim();
    const options = name ? { device_name: name } : {};
    const address = byId("recommendedIpv4")?.textContent?.trim();
    if (address && /^\d+\.\d+\.\d+\.\d+$/.test(address)) options.advertise_host = address;
    await api("/start", { method: "POST", body: JSON.stringify(options) });
    await refresh({ announce: true });
  });
}
async function disable() {
  await mutate(i18n.t("Turning Wi-Fi transfers off…"), async () => { await api("/stop", { method: "POST" }); await refresh({ announce: true }); });
}
async function invite() {
  await mutate(i18n.t("Creating a one-use pairing invitation…"), async () => {
    const result = await api("/invite", { method: "POST" });
    byId("transferInvitation").value = result.invitation;
    byId("transferInvitationPanel").hidden = false;
    const qr = byId("transferInvitationQr");
    qr.hidden = !result.qr_svg;
    byId("transferQrHelp").hidden = !result.qr_svg;
    if (result.qr_svg) qr.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.qr_svg)}`;
    else qr.removeAttribute("src");
    text("transferInvitationExpiry", result.expires_at ? i18n.t("Expires {time}", { time: dateLabel(result.expires_at) }) : i18n.t("Use this invitation promptly."));
    notice(i18n.t("Scan the QR code or paste the code into Pit Wall on your other device. Enable transfers there first."), "success");
  });
}
async function copyInvitation() {
  const field = byId("transferInvitation");
  if (!field.value) return;
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(field.value);
    else { field.focus(); field.select(); if (!document.execCommand("copy")) throw new Error("copy failed"); }
    notice(i18n.t("Pairing code copied. Paste it into Pit Wall on your other device."), "success");
  } catch { field.focus(); field.select(); notice(i18n.t("Select and copy the code above using your device’s Copy command.")); }
}
async function pair(event) {
  event.preventDefault(); const invitation = byId("transferPairCode").value.trim();
  if (!invitation) { notice(i18n.t("Paste the whole invitation from your other device first."), "error"); byId("transferPairCode").focus(); return; }
  await mutate(i18n.t("Connecting and checking the other device…"), async () => {
    const peer = await api("/pair", { method: "POST", body: JSON.stringify({ invitation, device_name: byId("transferDeviceName").value.trim() || undefined }) });
    byId("transferPairCode").value = "";
    await refresh(); notice(peer.name ? i18n.t("Paired with {name}. Browse its sessions below.", { name: `${peer.name}` }) : i18n.t("Paired with your other device. Browse its sessions below."), "success");
  });
}
async function revoke(peer) {
  if (!window.confirm(peer.name ? i18n.t("Remove the pairing with {name}? History already copied will stay on each device.", { name: `${peer.name}` }) : i18n.t("Remove the pairing with this device? History already copied will stay on each device."))) return;
  await mutate(i18n.t("Removing pairing…"), async () => {
    await api(`/peers/${encodeURIComponent(peer.id)}`, { method: "DELETE" });
    if (state.peer?.id === peer.id) closeCatalog();
    await refresh(); notice(i18n.t("Pairing removed. Saved history remains on this device."), "success");
  });
}

async function browse(peer) {
  const request = ++state.catalogRequest;
  state.peer = peer; state.sessions = []; state.selected.clear();
  byId("transferCatalog").hidden = false;
  text("transferCatalogHeading", peer.name ? i18n.t("Sessions on {name}", { name: `${peer.name}` }) : i18n.t("Sessions on your other device"));
  text("transferCatalogSummary", i18n.t("Loading completed sessions…"));
  byId("transferSessions").replaceChildren(); byId("transferSessionSearch").value = ""; updateSelection();
  try {
    const payload = await api(`/peers/${encodeURIComponent(peer.id)}/sessions`);
    if (request !== state.catalogRequest) return;
    state.sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
    const summaries = state.sessions.filter((session) => SUMMARY_ONLY.includes(completenessLevel(session))).length;
    text("transferCatalogSummary", summaries
      ? i18n.tp(state.sessions.length, "{count} completed session · {summaries} summary only. Select the sessions to copy here.", "{count} completed sessions · {summaries} summary only. Select the sessions to copy here.", { count: state.sessions.length, summaries })
      : i18n.tp(state.sessions.length, "{count} completed session. Select the sessions to copy here.", "{count} completed sessions. Select the sessions to copy here.", { count: state.sessions.length }));
    renderSessions();
  } catch (error) { if (request === state.catalogRequest) { text("transferCatalogSummary", error.message); notice(i18n.t("Could not load this device’s history. Check it is open with transfers enabled, then Browse sessions again."), "error"); } }
}
function closeCatalog() { ++state.catalogRequest; state.peer = null; state.sessions = []; state.selected.clear(); byId("transferCatalog").hidden = true; updateSelection(); }
function filteredSessions() {
  const query = byId("transferSessionSearch").value.trim().toLocaleLowerCase();
  return state.sessions.filter((session) => `${session.name || session.display_name || ""} ${session.track_name || ""} ${session.session_type || ""} ${sessionIdentity(session)}`.toLocaleLowerCase().includes(query));
}
function renderSessions() {
  const host = byId("transferSessions"); host.replaceChildren();
  const sessions = filteredSessions();
  if (!sessions.length) host.append(textElement("p", "muted", state.sessions.length ? i18n.t("No sessions match this filter.") : i18n.t("No completed sessions available on this device.")));
  for (const session of sessions) {
    const id = sessionIdentity(session); const available = Boolean(id) && session.transferable !== false;
    const row = textElement("label", "transfer-session transfer-check", "");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(id); checkbox.disabled = !available;
    const title = session.name || session.display_name || `${session.track_name || (session.track_id !== undefined ? i18n.t("Track {id}", { id: `${session.track_id}` }) : i18n.t("Recorded session"))}${session.session_type ? ` · ${session.session_type}` : ""}`;
    checkbox.setAttribute("aria-label", i18n.t("Select {title}", { title: `${title}` }));
    checkbox.addEventListener("change", () => { if (checkbox.checked) state.selected.add(id); else state.selected.delete(id); updateSelection(); });
    const details = textElement("span", "transfer-session-details", "");
    details.append(textElement("strong", "", title));
    const descriptors = [dateLabel(session.started_at ?? session.created_at ?? session.start_time)];
    if (session.lap_count !== undefined) descriptors.push(i18n.t("{count} laps", { count: `${session.lap_count}` }));
    if (session.driver_count !== undefined || session.drivers_observed !== undefined) descriptors.push(i18n.t("{count} drivers", { count: `${session.driver_count ?? session.drivers_observed}` }));
    if (session.trace_laps !== undefined) descriptors.push(i18n.t("{count} laps with traces", { count: `${session.trace_laps}` }));
    if (session.bytes !== undefined || session.size_bytes !== undefined) descriptors.push(formatTransferBytes(session.bytes ?? session.size_bytes));
    details.append(textElement("span", "field-help", descriptors.join(" · ")));
    if (!available) details.append(textElement("span", "field-help", session.unavailable_reason || i18n.t("This session cannot be transferred yet.")));
    const detail = sessionCompleteness(session); const chip = textElement("span", "state-chip", detail.label); chip.dataset.state = detail.tone;
    row.append(checkbox, details, chip); host.append(row);
  }
  updateSelection();
}
function updateSelection() {
  const count = selectedSessionIds(state.sessions, state.selected).length;
  text("transferSelectedCount", i18n.tp(count, "{count} session selected", "{count} sessions selected", { count }));
  if (byId("transferPull")) byId("transferPull").disabled = count === 0 || state.busy || activeJobs();
  const visible = filteredSessions().filter((session) => sessionIdentity(session) && session.transferable !== false);
  const checked = visible.filter((session) => state.selected.has(sessionIdentity(session))).length;
  byId("transferSelectAll").checked = visible.length > 0 && checked === visible.length;
  byId("transferSelectAll").indeterminate = checked > 0 && checked < visible.length;
  byId("transferSelectAll").disabled = visible.length === 0;
}
async function pull() {
  const ids = selectedSessionIds(state.sessions, state.selected); const peer = state.peer;
  if (!peer || !ids.length || activeJobs()) return;
  await mutate(i18n.tp(ids.length, "Starting transfer of {count} selected session…", "Starting transfer of {count} selected sessions…", { count: ids.length }), async () => {
    await api(`/peers/${encodeURIComponent(peer.id)}/pull`, { method: "POST", body: JSON.stringify({ session_ids: ids }) });
    await refresh(); notice(i18n.t("Transfer started. You can use other pages; keep both apps open until it finishes."), "success");
  });
}

function renderStage(card, job, outgoing = false) {
  const stage = transferStage(job, outgoing);
  const heading = stage.total === null ? stage.label : i18n.t("{stage} · {percent}% of this stage", { stage: stage.label, percent: Math.floor(100 * stage.done / stage.total) });
  card.append(textElement("div", "transfer-stage", heading));
  if (!state.stale || stage.total !== null) {
    const progress = document.createElement("progress");
    progress.setAttribute("aria-label", state.stale ? i18n.t("{progress} (last known)", { progress: heading }) : heading);
    if (stage.total !== null) { progress.max = stage.total; progress.value = stage.done; }
    card.append(progress);
  }
  card.append(textElement("div", "field-help", stage.detail));
}
function directionLabel(card, job, outgoing) {
  const peer = state.status?.peers?.find(peer => peer.id === job.peer_id);
  card.append(textElement("div", "transfer-direction", peer?.name ? (outgoing ? i18n.t("Sending to {name}", { name: `${peer.name}` }) : i18n.t("Receiving from {name}", { name: `${peer.name}` })) : (outgoing ? i18n.t("Sending to paired device") : i18n.t("Receiving from paired device"))));
  if (state.stale) card.append(textElement("p", "transfer-stale", i18n.t("Updates unavailable — last known progress shown.")));
}
function renderJobs(jobs, exports = []) {
  const signature = JSON.stringify([jobs, exports, state.stale, state.status?.peers]);
  if (signature === state.jobSignature) return;
  state.jobSignature = signature;
  const host = byId("transferJobs"); host.replaceChildren(); byId("transferJobsPanel").hidden = !jobs.length && !exports.length;
  for (const job of [...exports].reverse()) {
    const card = textElement("article", "transfer-job", ""); card.dataset.status = job.status; card.dataset.direction = "outgoing";
    directionLabel(card, job, true);
    const header = textElement("div", "section-heading", "");
    const label = { preparing: i18n.t("Preparing history"), ready: i18n.t("Ready to send"), sending: i18n.t("Sending history"), sent: i18n.t("File sent — check receiving device"), interrupted: i18n.t("Sending interrupted"), failed: i18n.t("Export needs attention"), expired: i18n.t("Export expired") }[job.status] || i18n.t("Checking export");
    header.append(textElement("strong", "", label), textElement("span", "field-help", i18n.t("{count} sessions", { count: `${job.session_ids?.length ?? 0}` }))); card.append(header);
    if (["preparing", "sending", "sent", "interrupted"].includes(job.status)) renderStage(card, job, true);
    const detail = { ready: i18n.t("Archive prepared. Waiting for the receiving device to download it."), sent: i18n.t("Sending is finished. The receiving device still needs to verify and import the archive; check it for the final result."), interrupted: i18n.t("The connection stopped. Retry on the receiving device to resume copying."), expired: i18n.t("This cached export has expired. Retry on the receiving device to prepare another.") }[job.status];
    if (detail) card.append(textElement("p", "field-help", detail));
    if (job.status === "failed") card.append(textElement("p", "field-help", job.error ? i18n.t("{error} Retry on the receiving device.", { error: `${job.error}` }) : i18n.t("Could not prepare history. Retry on the receiving device.")));
    host.append(card);
  }
  for (const job of [...jobs].reverse().slice(0, 8)) {
    const card = textElement("article", "transfer-job", ""); card.dataset.status = job.status; card.dataset.direction = "incoming";
    directionLabel(card, job, false);
    const header = textElement("div", "section-heading", "");
    header.append(textElement("strong", "", transferJobLabel(job.status)), textElement("span", "field-help", job.session_ids ? i18n.t("{count} sessions", { count: `${job.session_ids.length ?? ""}` }) : "")); card.append(header);
    if (!TERMINAL.has(job.status)) renderStage(card, job);
    if (job.status === "completed") {
      const result = job.result || {};
      const description = [];
      for (const [key, label] of [["imported_sessions", (count) => i18n.t("{count} added", { count })], ["skipped_sessions", (count) => i18n.t("{count} already present", { count })], ["conflicting_sessions", (count) => i18n.t("{count} conflicts preserved", { count })]]) {
        if (result[key] !== undefined) description.push(label(`${Array.isArray(result[key]) ? result[key].length : result[key]}`));
      }
      card.append(textElement("p", "field-help", description.length ? description.join(" · ") : i18n.t("Transfer finished. Your copied sessions are available in Library.")));
      if (Array.isArray(result.warnings)) for (const warning of result.warnings) card.append(textElement("p", "field-help", warning));
      const missing = result.missing_asset_count ?? result.missing_assets?.length ?? 0;
      if (missing) card.append(textElement("p", "field-help", i18n.t("{count} detail files were already missing on the source device. Available sessions and summaries were copied.", { count: `${missing}` })));
      card.append(action(i18n.t("Open Library"), openLibrary));
      if (!state.announcedJobs.has(job.id)) { state.announcedJobs.add(job.id); window.dispatchEvent(new CustomEvent("pitwall:historyimported", { detail: { job_id: job.id } })); }
    }
    if (job.status === "conflict") {
      card.append(textElement("p", "field-help", i18n.t("Existing history was left intact. A different version of a recording was received and its archive was saved for review.")));
      for (const conflict of job.result?.conflicts || []) card.append(textElement("p", "field-help", conflict.reason || conflict.message || i18n.t("A recording differs between these devices.")));
      if (job.result?.preserved_path) card.append(textElement("p", "field-help", i18n.t("Saved archive: {path}", { path: `${job.result.preserved_path}` })));
    }
    if (job.status === "failed") {
      card.append(textElement("p", "field-help", typeof job.error === "string" ? job.error : transferError(job.error, i18n.t("Transfer interrupted. Check both devices are connected."))));
      card.append(action(i18n.t("Retry transfer"), () => retry(job)));
    }
    host.append(card);
  }
}
async function retry(job) {
  await mutate(i18n.t("Resuming the transfer…"), async () => { await api(`/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" }); await refresh(); notice(i18n.t("Transfer restarted. Keep both apps open until import completes.")); });
}
function openLibrary() { document.querySelector('.tab[data-page="analysis"]')?.click(); byId("tab-library")?.click(); byId("libraryRefresh")?.click(); }
function acceptInvitation(invitation) {
  if (typeof invitation !== "string" || !invitation.trim() || invitation.length > 8192) return false;
  byId("transferPairCode").value = invitation.trim();
  openTransfers();
  notice(state.status?.running ? i18n.t("Invitation received. Tap Pair devices to connect.") : i18n.t("Invitation received. Enable Wi-Fi transfers on this device, then tap Pair devices."));
  return true;
}
function openTransfers() { byId("tab-connection")?.click(); byId("transferPanel")?.scrollIntoView({ block: "start" }); byId("transferPanel")?.focus({ preventScroll: true }); }
function schedule() {
  clearTimeout(state.timer); state.timer = null;
  if (document.hidden || (!state.active && !activeTransfers())) return;
  state.timer = setTimeout(async () => { if (!state.busy) await refresh(); schedule(); }, activeTransfers() ? 1500 : 5000);
}
function setActive(value) { state.active = value; if (value && !document.hidden) void refresh({ announce: !state.status }).finally(schedule); else schedule(); }

function initialize() {
  if (!byId("transferPanel")) return;
  byId("transferEnableForm").addEventListener("submit", enable);
  byId("transferDisable").addEventListener("click", disable);
  byId("transferInvite").addEventListener("click", invite);
  byId("transferCopyInvitation").addEventListener("click", copyInvitation);
  byId("transferPairForm").addEventListener("submit", pair);
  byId("transferRefresh").addEventListener("click", () => void refresh({ announce: true }));
  byId("transferCloseCatalog").addEventListener("click", closeCatalog);
  byId("transferSessionSearch").addEventListener("input", renderSessions);
  byId("transferSelectAll").addEventListener("change", (event) => { for (const session of filteredSessions()) { const id = sessionIdentity(session); if (!id || session.transferable === false) continue; if (event.target.checked) state.selected.add(id); else state.selected.delete(id); } renderSessions(); });
  byId("transferPull").addEventListener("click", pull);
  byId("libraryOpenTransfers").addEventListener("click", openTransfers);
  byId("connectionRunChecks").addEventListener("click", () => { byId("diagnoseNetwork")?.click(); byId("diagnoseNetwork")?.scrollIntoView({ block: "center" }); });
  window.addEventListener("pitwall:pagechange", (event) => setActive(event.detail?.page === "connection"));
  document.addEventListener("visibilitychange", () => { if (document.hidden) schedule(); else if (state.active || activeTransfers()) void refresh().finally(schedule); });
  window.PitWallTransfers = { acceptInvitation, open: openTransfers };
  setActive(byId("connection").classList.contains("active"));
}
if (HAS_DOM) { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true }); else initialize(); }
