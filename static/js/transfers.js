const API_BASE = "/api/v1/transfers";
const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";
const TERMINAL = new Set(["completed", "failed", "conflict"]);
const state = { active: false, timer: null, refreshing: false, busy: false, status: null, peer: null, sessions: [], selected: new Set(), catalogRequest: 0, peerSignature: "", jobSignature: "", announcedJobs: new Set() };
const byId = (id) => HAS_DOM ? document.getElementById(id) : null;

export function transferError(payload, fallback = "The request could not be completed.") {
  const detail = payload?.detail ?? payload;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail?.message === "string") return detail.message;
  return fallback;
}

export function formatTransferBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return "Size unavailable";
  if (size < 1024) return `${Math.round(size)} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${(size / 1024 ** 3).toFixed(2)} GB`;
}

export function transferJobLabel(value) {
  return { queued: "Waiting to start", preparing: "Preparing history", downloading: "Copying history", importing: "Adding sessions to this device", completed: "History copied", failed: "Transfer needs attention", conflict: "History needs review" }[value] || "Checking transfer";
}

export function sessionIdentity(session) { return String(session?.session_id ?? session?.id ?? ""); }

export function sessionCompleteness(session) {
  const detail = session?.completeness ?? session?.telemetry_completeness ?? session?.detail_level;
  const value = typeof detail === "object" ? detail?.level ?? detail?.status : detail;
  if (["full", "complete", "full_telemetry"].includes(value)) return { label: "Full telemetry", tone: "healthy" };
  if (["partial", "some", "partial_telemetry"].includes(value)) return { label: "Partial telemetry", tone: "warning" };
  if (["summary", "summary_only", "none"].includes(value)) return { label: "Summary only", tone: "warning" };
  if (value === "cataloged-detail") return { label: "Lap detail recorded", tone: "neutral" };
  return { label: "Detail not reported", tone: "neutral" };
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
  if (!value) return "Date unavailable";
  const parsed = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(parsed.getTime()) ? "Date unavailable" : parsed.toLocaleString();
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
    if (!response.ok) throw new Error(transferError(payload, response.status === 404 ? "Wi-Fi transfers are unavailable in this build. Update both devices to a compatible release." : `Transfer service returned ${response.status}.`));
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request timed out. Check both apps are open on the same network, then refresh before trying again.");
    if (error instanceof TypeError) throw new Error("Cannot reach this device’s transfer service. Check Pit Wall is still running.");
    throw error;
  } finally { clearTimeout(timeout); }
}

function activeJobs() { return (state.status?.jobs || []).some((job) => !TERMINAL.has(job.status)); }
function setBusy(value) {
  state.busy = value;
  for (const id of ["transferEnable", "transferDisable", "transferInvite", "transferPair", "transferRefresh"]) {
    if (byId(id)) byId(id).disabled = value;
  }
  byId("transferDeviceName").disabled = value || Boolean(state.status?.running);
  byId("transferDisable").disabled = value;
  byId("transferDisable").title = activeJobs() ? "Pause copying; an import already committing will finish safely." : "Turn off Wi-Fi transfers";
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
  const running = Boolean(payload?.running);
  byId("transferBadge").dataset.state = running ? "healthy" : "neutral";
  text("transferBadge", running ? "Wi-Fi transfers on" : "Off");
  byId("transferEnable").hidden = running;
  byId("transferDisable").hidden = !running;
  byId("transferWorkspace").hidden = !running;
  const name = byId("transferDeviceName");
  if (document.activeElement !== name && payload.device_name) name.value = payload.device_name;
  name.disabled = running || state.busy;
  text("transferEndpoint", running ? `Available on your home network${payload.endpoint ? ` · ${payload.endpoint}` : ""}. Keep both apps open during transfers.` : "Enable transfers on both devices. Turning transfers off does not remove your saved history.");
  if (!running) { byId("transferInvitation").value = ""; byId("transferInvitationPanel").hidden = true; byId("transferInvitationQr").removeAttribute("src"); }
  renderPeers(payload.peers || []);
  renderJobs(payload.jobs || []);
  setBusy(state.busy);
}

function renderPeers(peers) {
  const signature = JSON.stringify(peers);
  if (signature === state.peerSignature) return;
  state.peerSignature = signature;
  const host = byId("transferPeers"); host.replaceChildren();
  if (!peers.length) { host.append(textElement("p", "muted", "No paired devices yet. Create or paste an invitation above.")); return; }
  for (const peer of peers) {
    const card = textElement("article", "transfer-peer", "");
    card.append(textElement("strong", "", peer.name || "Paired device"), textElement("div", "transfer-peer-address", peer.endpoint || ""));
    const actions = textElement("div", "transfer-actions", "");
    actions.append(action("Browse sessions", () => browse(peer)), action("Remove pairing", () => revoke(peer)));
    card.append(actions); host.append(card);
  }
}

async function refresh({ announce = false } = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const payload = await api("/status"); renderStatus(payload);
    if (payload.error) notice(payload.error, "error");
    else if (announce) notice(payload.running ? "Ready. Pair another device or browse an existing pairing below." : "Wi-Fi transfers are off. Give this device a name, then enable transfers.");
  } catch (error) { notice(error.message, "error"); text("transferBadge", "Unavailable"); byId("transferBadge").dataset.state = "error"; }
  finally { state.refreshing = false; }
}

async function enable(event) {
  event.preventDefault();
  await mutate("Starting encrypted Wi-Fi transfers…", async () => {
    const name = byId("transferDeviceName").value.trim();
    const options = name ? { device_name: name } : {};
    const address = byId("recommendedIpv4")?.textContent?.trim();
    if (address && /^\d+\.\d+\.\d+\.\d+$/.test(address)) options.advertise_host = address;
    await api("/start", { method: "POST", body: JSON.stringify(options) });
    await refresh({ announce: true });
  });
}
async function disable() {
  await mutate("Turning Wi-Fi transfers off…", async () => { await api("/stop", { method: "POST" }); await refresh({ announce: true }); });
}
async function invite() {
  await mutate("Creating a one-use pairing invitation…", async () => {
    const result = await api("/invite", { method: "POST" });
    byId("transferInvitation").value = result.invitation;
    byId("transferInvitationPanel").hidden = false;
    const qr = byId("transferInvitationQr");
    qr.hidden = !result.qr_svg;
    byId("transferQrHelp").hidden = !result.qr_svg;
    if (result.qr_svg) qr.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.qr_svg)}`;
    else qr.removeAttribute("src");
    text("transferInvitationExpiry", result.expires_at ? `Expires ${dateLabel(result.expires_at)}` : "Use this invitation promptly.");
    notice("Scan the QR code or paste the code into Pit Wall on your other device. Enable transfers there first.", "success");
  });
}
async function copyInvitation() {
  const field = byId("transferInvitation");
  if (!field.value) return;
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(field.value);
    else { field.focus(); field.select(); if (!document.execCommand("copy")) throw new Error("copy failed"); }
    notice("Pairing code copied. Paste it into Pit Wall on your other device.", "success");
  } catch { field.focus(); field.select(); notice("Select and copy the code above using your device’s Copy command."); }
}
async function pair(event) {
  event.preventDefault(); const invitation = byId("transferPairCode").value.trim();
  if (!invitation) { notice("Paste the whole invitation from your other device first.", "error"); byId("transferPairCode").focus(); return; }
  await mutate("Connecting and checking the other device…", async () => {
    const peer = await api("/pair", { method: "POST", body: JSON.stringify({ invitation, device_name: byId("transferDeviceName").value.trim() || undefined }) });
    byId("transferPairCode").value = "";
    await refresh(); notice(`Paired with ${peer.name || "your other device"}. Browse its sessions below.`, "success");
  });
}
async function revoke(peer) {
  if (!window.confirm(`Remove the pairing with ${peer.name || "this device"}? History already copied will stay on each device.`)) return;
  await mutate("Removing pairing…", async () => {
    await api(`/peers/${encodeURIComponent(peer.id)}`, { method: "DELETE" });
    if (state.peer?.id === peer.id) closeCatalog();
    await refresh(); notice("Pairing removed. Saved history remains on this device.", "success");
  });
}

async function browse(peer) {
  const request = ++state.catalogRequest;
  state.peer = peer; state.sessions = []; state.selected.clear();
  byId("transferCatalog").hidden = false;
  text("transferCatalogHeading", `Sessions on ${peer.name || "your other device"}`);
  text("transferCatalogSummary", "Loading completed sessions…");
  byId("transferSessions").replaceChildren(); byId("transferSessionSearch").value = ""; updateSelection();
  try {
    const payload = await api(`/peers/${encodeURIComponent(peer.id)}/sessions`);
    if (request !== state.catalogRequest) return;
    state.sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
    const summaries = state.sessions.filter((session) => sessionCompleteness(session).label === "Summary only").length;
    text("transferCatalogSummary", `${state.sessions.length} completed session${state.sessions.length === 1 ? "" : "s"}${summaries ? ` · ${summaries} summary only` : ""}. Select the sessions to copy here.`);
    renderSessions();
  } catch (error) { if (request === state.catalogRequest) { text("transferCatalogSummary", error.message); notice("Could not load this device’s history. Check it is open with transfers enabled, then Browse sessions again.", "error"); } }
}
function closeCatalog() { ++state.catalogRequest; state.peer = null; state.sessions = []; state.selected.clear(); byId("transferCatalog").hidden = true; updateSelection(); }
function filteredSessions() {
  const query = byId("transferSessionSearch").value.trim().toLocaleLowerCase();
  return state.sessions.filter((session) => `${session.name || session.display_name || ""} ${session.track_name || ""} ${session.session_type || ""} ${sessionIdentity(session)}`.toLocaleLowerCase().includes(query));
}
function renderSessions() {
  const host = byId("transferSessions"); host.replaceChildren();
  const sessions = filteredSessions();
  if (!sessions.length) host.append(textElement("p", "muted", state.sessions.length ? "No sessions match this filter." : "No completed sessions available on this device."));
  for (const session of sessions) {
    const id = sessionIdentity(session); const available = Boolean(id) && session.transferable !== false;
    const row = textElement("label", "transfer-session transfer-check", "");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(id); checkbox.disabled = !available;
    const title = session.name || session.display_name || `${session.track_name || (session.track_id !== undefined ? `Track ${session.track_id}` : "Recorded session")}${session.session_type ? ` · ${session.session_type}` : ""}`;
    checkbox.setAttribute("aria-label", `Select ${title}`);
    checkbox.addEventListener("change", () => { if (checkbox.checked) state.selected.add(id); else state.selected.delete(id); updateSelection(); });
    const details = textElement("span", "transfer-session-details", "");
    details.append(textElement("strong", "", title));
    const descriptors = [dateLabel(session.started_at ?? session.created_at ?? session.start_time)];
    if (session.lap_count !== undefined) descriptors.push(`${session.lap_count} laps`);
    if (session.driver_count !== undefined || session.drivers_observed !== undefined) descriptors.push(`${session.driver_count ?? session.drivers_observed} drivers`);
    if (session.trace_laps !== undefined) descriptors.push(`${session.trace_laps} laps with traces`);
    if (session.bytes !== undefined || session.size_bytes !== undefined) descriptors.push(formatTransferBytes(session.bytes ?? session.size_bytes));
    details.append(textElement("span", "field-help", descriptors.join(" · ")));
    if (!available) details.append(textElement("span", "field-help", session.unavailable_reason || "This session cannot be transferred yet."));
    const detail = sessionCompleteness(session); const chip = textElement("span", "state-chip", detail.label); chip.dataset.state = detail.tone;
    row.append(checkbox, details, chip); host.append(row);
  }
  updateSelection();
}
function updateSelection() {
  const count = selectedSessionIds(state.sessions, state.selected).length;
  text("transferSelectedCount", `${count} session${count === 1 ? "" : "s"} selected`);
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
  await mutate(`Starting transfer of ${ids.length} selected session${ids.length === 1 ? "" : "s"}…`, async () => {
    await api(`/peers/${encodeURIComponent(peer.id)}/pull`, { method: "POST", body: JSON.stringify({ session_ids: ids }) });
    await refresh(); notice("Transfer started. You can use other pages; keep both apps open until it finishes.", "success");
  });
}

function renderJobs(jobs) {
  const signature = JSON.stringify(jobs);
  if (signature === state.jobSignature) return;
  state.jobSignature = signature;
  const host = byId("transferJobs"); host.replaceChildren(); byId("transferJobsPanel").hidden = !jobs.length;
  for (const job of [...jobs].reverse().slice(0, 8)) {
    const card = textElement("article", "transfer-job", ""); card.dataset.status = job.status;
    const header = textElement("div", "section-heading", "");
    header.append(textElement("strong", "", transferJobLabel(job.status)), textElement("span", "field-help", `${job.session_ids?.length ?? ""}${job.session_ids ? " sessions" : ""}`)); card.append(header);
    if (!TERMINAL.has(job.status)) {
      const progress = document.createElement("progress"); progress.setAttribute("aria-label", transferJobLabel(job.status));
      if (job.status === "downloading" && Number(job.total_bytes) > 0) { progress.max = Number(job.total_bytes); progress.value = Math.min(Number(job.bytes_received) || 0, progress.max); }
      card.append(progress);
    }
    if (job.status === "downloading") card.append(textElement("div", "field-help", `${formatTransferBytes(job.bytes_received ?? 0)}${job.total_bytes ? ` of ${formatTransferBytes(job.total_bytes)}` : " copied"}`));
    if (job.status === "completed") {
      const result = job.result || {};
      const description = [];
      for (const [key, label] of [["imported_sessions", "added"], ["skipped_sessions", "already present"], ["conflicting_sessions", "conflicts preserved"]]) {
        if (result[key] !== undefined) description.push(`${Array.isArray(result[key]) ? result[key].length : result[key]} ${label}`);
      }
      card.append(textElement("p", "field-help", description.length ? description.join(" · ") : "Transfer finished. Your copied sessions are available in Library."));
      if (Array.isArray(result.warnings)) for (const warning of result.warnings) card.append(textElement("p", "field-help", warning));
      const missing = result.missing_asset_count ?? result.missing_assets?.length ?? 0;
      if (missing) card.append(textElement("p", "field-help", `${missing} detail files were already missing on the source device. Available sessions and summaries were copied.`));
      card.append(action("Open Library", openLibrary));
      if (!state.announcedJobs.has(job.id)) { state.announcedJobs.add(job.id); window.dispatchEvent(new CustomEvent("pitwall:historyimported", { detail: { job_id: job.id } })); }
    }
    if (job.status === "conflict") {
      card.append(textElement("p", "field-help", "Existing history was left intact. A different version of a recording was received and its archive was saved for review."));
      for (const conflict of job.result?.conflicts || []) card.append(textElement("p", "field-help", conflict.reason || conflict.message || "A recording differs between these devices."));
      if (job.result?.preserved_path) card.append(textElement("p", "field-help", `Saved archive: ${job.result.preserved_path}`));
    }
    if (job.status === "failed") {
      card.append(textElement("p", "field-help", typeof job.error === "string" ? job.error : transferError(job.error, "Transfer interrupted. Check both devices are connected.")));
      card.append(action("Retry transfer", () => retry(job)));
    }
    host.append(card);
  }
}
async function retry(job) {
  await mutate("Resuming the transfer…", async () => { await api(`/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" }); await refresh(); notice("Transfer restarted. Keep both apps open until import completes."); });
}
function openLibrary() { document.querySelector('.tab[data-page="analysis"]')?.click(); byId("tab-library")?.click(); byId("libraryRefresh")?.click(); }
function acceptInvitation(invitation) {
  if (typeof invitation !== "string" || !invitation.trim() || invitation.length > 8192) return false;
  byId("transferPairCode").value = invitation.trim();
  openTransfers();
  notice(state.status?.running ? "Invitation received. Tap Pair devices to connect." : "Invitation received. Enable Wi-Fi transfers on this device, then tap Pair devices.");
  return true;
}
function openTransfers() { byId("tab-connection")?.click(); byId("transferPanel")?.scrollIntoView({ block: "start" }); byId("transferPanel")?.focus({ preventScroll: true }); }
function schedule() {
  clearTimeout(state.timer); state.timer = null;
  if (document.hidden || (!state.active && !activeJobs())) return;
  state.timer = setTimeout(async () => { if (!state.busy) await refresh(); schedule(); }, activeJobs() ? 1500 : 5000);
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
  document.addEventListener("visibilitychange", () => { if (document.hidden) schedule(); else if (state.active || activeJobs()) void refresh().finally(schedule); });
  window.PitWallTransfers = { acceptInvitation, open: openTransfers };
  setActive(byId("connection").classList.contains("active"));
}
if (HAS_DOM) { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true }); else initialize(); }
