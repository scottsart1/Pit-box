const API_BASE = "/api/v1/network";
const POLL_INTERVAL_MS = 2000;
const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";

const uiState = {
  active: false,
  pollTimer: null,
  status: null,
  interfaces: null,
  forwarders: [],
  credentials: null,
};

const byId = (id) => (HAS_DOM ? document.getElementById(id) : null);

export function listenerStateLabel(value) {
  return {
    off: "Off",
    listening: "Listening — waiting for telemetry",
    receiving: "Receiving telemetry",
    stale: "Telemetry stale",
    error: "Listener error",
  }[value] || "Unavailable";
}

export function parsePacketIds(value) {
  const source = String(value ?? "").trim();
  if (!source || source.toLowerCase() === "all") return "all";
  const parts = source.split(",").map((part) => part.trim()).filter(Boolean);
  if (!parts.length) return "all";
  const packetIds = [...new Set(parts.map((part) => Number(part)))].sort((a, b) => a - b);
  if (packetIds.some((packetId) => !Number.isInteger(packetId) || packetId < 0 || packetId > 255)) {
    throw new Error("Packet IDs must be whole numbers from 0 to 255, or ‘all’.");
  }
  return packetIds;
}

export function formatAge(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(Number(milliseconds))) return "Unavailable";
  const value = Math.max(0, Number(milliseconds));
  if (value < 1000) return `${Math.round(value)} ms ago`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s ago`;
  return `${Math.floor(value / 60000)} min ago`;
}

export function formatCount(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "Unavailable";
  return new Intl.NumberFormat().format(Number(value));
}

export function apiErrorMessage(payload, fallback = "The request could not be completed.") {
  const detail = payload?.detail ?? payload;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail.message === "string" && detail.message.trim()) return detail.message;
  if (typeof payload?.message === "string" && payload.message.trim()) return payload.message;
  return fallback;
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "same-origin" });
  } catch (error) {
    throw new Error(`Connection service is unreachable: ${error instanceof Error ? error.message : String(error)}`);
  }
  let payload = null;
  if (response.status !== 204) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const unavailable = response.status === 404
      ? "Connection services are unavailable in this build. Live, Review, and Setup remain available."
      : `Network request failed (${response.status}).`;
    const error = new Error(apiErrorMessage(payload, unavailable));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setText(id, value, fallback = "Unavailable") {
  const element = byId(id);
  if (element) element.textContent = value === null || value === undefined || value === "" ? fallback : String(value);
}

function setControlValue(id, value, fallback = "") {
  const element = byId(id);
  if (element) element.value = value === null || value === undefined ? fallback : String(value);
}

function setNotice(id, message, tone = "info") {
  const element = byId(id);
  if (!element) return;
  element.textContent = message || "";
  element.dataset.tone = tone;
}

function clearChildren(element) {
  if (element) element.replaceChildren();
}

function textElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function button(text, ariaLabel, handler, className = "button ghost") {
  const element = textElement("button", className, text);
  element.type = "button";
  if (ariaLabel) element.setAttribute("aria-label", ariaLabel);
  element.addEventListener("click", handler);
  return element;
}

function formatEndpoint(source) {
  if (!source) return "Unavailable";
  const host = source.ip ?? source.host ?? source.address ?? source.source_ip;
  const port = source.port ?? source.source_port;
  if (!host) return "Unavailable";
  return port === null || port === undefined ? String(host) : `${host}:${port}`;
}

function renderWarnings(warnings = []) {
  const container = byId("networkWarnings");
  if (!container) return;
  clearChildren(container);
  const unique = [...new Set(warnings.filter((item) => typeof item === "string" && item.trim()))];
  if (!unique.length) return;
  const title = textElement("strong", "", unique.length === 1 ? "Connection warning" : "Connection warnings");
  const list = document.createElement("ul");
  unique.forEach((warning) => list.append(textElement("li", "", warning)));
  container.append(title, list);
}

function renderPacketHealth(packets = []) {
  const body = byId("packetHealthBody");
  if (!body) return;
  clearChildren(body);
  if (!packets.length) {
    const row = document.createElement("tr");
    const cell = textElement("td", "empty", "No packet data available. Listening alone does not prove that the PS5 is sending.");
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
    return;
  }
  [...packets].sort((a, b) => Number(a.packet_id) - Number(b.packet_id)).forEach((packet) => {
    const row = document.createElement("tr");
    const packetName = packet.packet_name ? String(packet.packet_name).replaceAll("_", " ") : `packet ${packet.packet_id}`;
    const state = String(packet.status || "unavailable").toLowerCase();
    const stateCell = document.createElement("td");
    const chip = textElement("span", "state-chip", state === "healthy" ? "Healthy" : state.replaceAll("_", " "));
    chip.dataset.state = state === "healthy" ? "healthy" : state.includes("error") || state.includes("invalid") ? "error" : "warning";
    stateCell.append(chip);
    const hz = Number.isFinite(Number(packet.observed_hz_10s)) ? `${Number(packet.observed_hz_10s).toFixed(1)} Hz` : "Unavailable";
    const gap = `${formatCount(packet.provisional_gap)} / ${formatCount(packet.confirmed_lost)}`;
    [
      textElement("th", "", packetName),
      stateCell,
      textElement("td", "", hz),
      textElement("td", "", formatAge(packet.last_age_ms)),
      textElement("td", "", formatCount(packet.received)),
      textElement("td", "", gap),
      textElement("td", "", formatCount(packet.out_of_order)),
      textElement("td", "", formatCount(packet.duplicates)),
    ].forEach((cell, index) => {
      if (index === 0) cell.scope = "row";
      row.append(cell);
    });
    body.append(row);
  });
}

function renderStatus(payload) {
  uiState.status = payload;
  const listener = payload?.listener || {};
  const listenerState = listener.state || "off";
  const badge = byId("connectionStateBadge");
  if (badge) badge.dataset.state = listenerState;
  setText("connectionStateText", listenerStateLabel(listenerState));
  setText("recommendedPort", listener.port ?? 20777);

  const recommendation = payload?.recommendation || null;
  const recommendationIp = recommendation?.console_destination_ipv4 || uiState.interfaces?.recommended_ipv4 || null;
  setText("recommendedIpv4", recommendationIp);
  const copyIp = byId("copyRecommendedIp");
  if (copyIp) copyIp.disabled = !recommendationIp;
  const interfaceMatch = uiState.interfaces?.interfaces?.find((item) => item.id === recommendation?.adapter_id);
  const confidence = recommendation?.confidence ? ` · ${recommendation.confidence} confidence` : "";
  setText("recommendedAdapter", interfaceMatch ? `${interfaceMatch.name}${confidence}` : recommendation?.adapter_id ? `${recommendation.adapter_id}${confidence}` : "No adapter recommendation yet.");

  if (document.activeElement !== byId("listenerBindHost")) setControlValue("listenerBindHost", listener.bind_host || "0.0.0.0", "0.0.0.0");
  if (document.activeElement !== byId("listenerPort")) setControlValue("listenerPort", listener.port ?? 20777, "20777");
  setText("networkSource", formatEndpoint(payload?.source));
  setText("networkFormat", payload?.game?.packet_format ?? payload?.game?.format);
  setText("networkSessionUid", payload?.game?.session_uid);
  setText("networkLastPacket", formatAge(listener.last_valid_packet_age_ms));

  const startButton = byId("listenerStart");
  const stopButton = byId("listenerStop");
  if (startButton) startButton.textContent = listenerState === "off" || listenerState === "error" ? "Start listening" : "Restart listener";
  if (stopButton) stopButton.disabled = listenerState === "off";

  const listenerMessages = {
    off: "Listener is off. Start it when you are ready to receive telemetry.",
    listening: "Listening successfully, but no recent valid F1 telemetry has arrived yet.",
    receiving: "Valid F1 telemetry is arriving from the game.",
    stale: "Telemetry arrived previously but is now older than its freshness budget.",
    error: listener.error || "The UDP listener reported an error.",
  };
  const tone = listenerState === "receiving" ? "success" : listenerState === "error" ? "error" : "info";
  setNotice("connectionApiStatus", listenerMessages[listenerState] || "Connection state unavailable.", tone);
  renderWarnings([...(payload?.warnings || []), ...(uiState.interfaces?.warnings || [])]);
  renderPacketHealth(payload?.packets || []);
  if (Array.isArray(payload?.forwarders)) {
    uiState.forwarders = payload.forwarders;
    renderForwarders(uiState.forwarders);
  }
}

function renderInterfaces(payload) {
  uiState.interfaces = payload;
  const container = byId("networkInterfaces");
  if (!container) return;
  clearChildren(container);
  const interfaces = [...(payload?.interfaces || [])].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  if (!interfaces.length) {
    container.append(textElement("div", "empty", "No usable IPv4 adapters were reported. Check that Wi-Fi or Ethernet is connected."));
    setText("recommendedIpv4", null);
    const copy = byId("copyRecommendedIp");
    if (copy) copy.disabled = true;
    return;
  }
  interfaces.forEach((item) => {
    const card = document.createElement("article");
    card.className = "adapter-card";
    const recommended = item.id === payload.recommended_adapter_id && item.ipv4 === payload.recommended_ipv4;
    card.dataset.recommended = String(recommended);
    const header = document.createElement("div");
    header.className = "adapter-head";
    const identity = document.createElement("div");
    identity.append(textElement("strong", "", item.name || item.description || "Network adapter"));
    identity.append(textElement("div", "adapter-address", item.ipv4 ? `${item.ipv4}${item.prefix_length === null || item.prefix_length === undefined ? "" : `/${item.prefix_length}`}` : "IPv4 unavailable"));
    const stateText = item.operational ? "Active" : "Inactive";
    const stateChip = textElement("span", "state-chip", stateText);
    stateChip.dataset.state = item.operational ? "healthy" : "warning";
    header.append(identity, stateChip);
    card.append(header);
    if (item.description && item.description !== item.name) card.append(textElement("div", "field-help", item.description));
    const tags = document.createElement("div");
    tags.className = "tag-row";
    if (recommended) tags.append(textElement("span", "tag-chip recommended", "Recommended for PS5"));
    if (item.pinned) tags.append(textElement("span", "tag-chip pinned", "Pinned choice"));
    if (item.previously_worked) tags.append(textElement("span", "tag-chip", "Valid F1 traffic seen before"));
    if (item.default_gateway) tags.append(textElement("span", "tag-chip", "Default route"));
    tags.append(textElement("span", "tag-chip", String(item.kind || "adapter")));
    card.append(tags);
    if (item.reasons?.length) {
      const reasons = document.createElement("ul");
      reasons.className = "reason-list";
      item.reasons.forEach((reason) => reasons.append(textElement("li", "", reason)));
      card.append(reasons);
    }
    container.append(card);
  });
  const recommendationIp = uiState.status?.recommendation?.console_destination_ipv4 || payload.recommended_ipv4;
  setText("recommendedIpv4", recommendationIp);
  const copy = byId("copyRecommendedIp");
  if (copy) copy.disabled = !recommendationIp;
  const recommended = interfaces.find((item) => item.id === payload.recommended_adapter_id && item.ipv4 === payload.recommended_ipv4);
  setText("recommendedAdapter", recommended ? `${recommended.name}${recommended.pinned ? " · pinned" : ""}` : "No adapter recommendation yet.");
  renderWarnings([...(payload.warnings || []), ...(uiState.status?.warnings || [])]);
}

function targetPacketLabel(packetIds) {
  return packetIds === "all" ? "All known packets" : `Packet IDs ${packetIds.join(", ")}`;
}

function renderForwarders(targets = []) {
  const container = byId("forwarderList");
  if (!container) return;
  clearChildren(container);
  if (!targets.length) {
    container.append(textElement("div", "empty", "No forwarding targets configured. Local telemetry ingestion is unaffected."));
    return;
  }
  targets.forEach((target) => {
    const card = document.createElement("article");
    card.className = "forwarder-card";
    const head = document.createElement("div");
    head.className = "forwarder-head";
    const identity = document.createElement("div");
    identity.append(textElement("strong", "", target.label || target.id));
    identity.append(textElement("div", "adapter-address", `${target.host}:${target.port}`));
    if (target.resolved_address && target.resolved_address !== target.host) identity.append(textElement("span", "field-help", `Resolves to ${target.resolved_address}`));
    const status = textElement("span", "state-chip", target.enabled ? "Enabled" : "Disabled");
    status.dataset.state = target.enabled ? (target.last_error ? "error" : "healthy") : "warning";
    head.append(identity, status);
    card.append(head, textElement("div", "field-help", `${targetPacketLabel(target.packet_ids)}${target.forward_unknown_packets ? " · unknown packets included" : ""}`));
    const counters = document.createElement("div");
    counters.className = "counter-grid";
    [
      ["Packets sent", formatCount(target.packets_sent)],
      ["Bytes sent", formatCount(target.bytes_sent)],
      ["Queue drops", formatCount(target.queue_drops)],
      ["Socket errors", formatCount(target.socket_errors)],
    ].forEach(([label, value]) => {
      const item = document.createElement("div");
      item.append(textElement("span", "", label), textElement("strong", "", value));
      counters.append(item);
    });
    card.append(counters);
    if (target.last_success) card.append(textElement("div", "field-help", `Last successful send: ${new Date(target.last_success).toLocaleString()}`));
    if (target.last_error) card.append(textElement("div", "error small", `Latest socket error: ${target.last_error}`));
    const actions = document.createElement("div");
    actions.className = "forwarder-actions";
    actions.append(
      button("Edit", `Edit ${target.label}`, () => editForwarder(target)),
      button(target.enabled ? "Disable" : "Enable", `${target.enabled ? "Disable" : "Enable"} ${target.label}`, () => toggleForwarder(target)),
      button("Delete", `Delete ${target.label}`, () => deleteForwarder(target), "button ghost danger-button"),
    );
    card.append(actions);
    container.append(card);
  });
}

function resetForwarderForm() {
  const form = byId("forwarderForm");
  if (!form) return;
  form.reset();
  setControlValue("forwarderId", "");
  byId("forwarderEnabled").checked = true;
  byId("forwarderPort").value = "20778";
  byId("saveForwarder").textContent = "Add target";
  byId("cancelForwarderEdit").hidden = true;
  setNotice("forwarderFormStatus", "");
}

function editForwarder(target) {
  byId("forwarderId").value = target.id;
  byId("forwarderLabel").value = target.label;
  byId("forwarderHost").value = target.host;
  byId("forwarderPort").value = target.port;
  byId("forwarderPacketIds").value = target.packet_ids === "all" ? "all" : target.packet_ids.join(", ");
  byId("forwarderEnabled").checked = Boolean(target.enabled);
  byId("forwardUnknownPackets").checked = Boolean(target.forward_unknown_packets);
  byId("forwarderConfirmPublic").checked = false;
  byId("saveForwarder").textContent = "Save target";
  byId("cancelForwarderEdit").hidden = false;
  setNotice("forwarderFormStatus", `Editing ${target.label}. Public destinations must be reconfirmed when changed.`);
  byId("forwarderLabel").focus();
}

async function toggleForwarder(target) {
  setNotice("forwarderFormStatus", `${target.enabled ? "Disabling" : "Enabling"} ${target.label}…`);
  try {
    await apiRequest(`/forwarders/${encodeURIComponent(target.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !target.enabled }),
    });
    await refreshForwarders();
    setNotice("forwarderFormStatus", `${target.label} ${target.enabled ? "disabled" : "enabled"}.`, "success");
  } catch (error) {
    setNotice("forwarderFormStatus", error.message, "error");
  }
}

async function deleteForwarder(target) {
  if (!window.confirm(`Delete forwarding target “${target.label}” (${target.host}:${target.port})?`)) return;
  setNotice("forwarderFormStatus", `Deleting ${target.label}…`);
  try {
    await apiRequest(`/forwarders/${encodeURIComponent(target.id)}`, { method: "DELETE" });
    if (byId("forwarderId").value === target.id) resetForwarderForm();
    await refreshForwarders();
    setNotice("forwarderFormStatus", `${target.label} deleted.`, "success");
  } catch (error) {
    setNotice("forwarderFormStatus", error.message, "error");
  }
}

async function refreshForwarders() {
  const targets = await apiRequest("/forwarders");
  uiState.forwarders = Array.isArray(targets) ? targets : [];
  renderForwarders(uiState.forwarders);
}

async function refreshStatus({ quiet = false } = {}) {
  try {
    const payload = await apiRequest("/status");
    renderStatus(payload);
  } catch (error) {
    if (!quiet || !uiState.status) {
      setNotice("connectionApiStatus", error.message, "error");
      const badge = byId("connectionStateBadge");
      if (badge) badge.dataset.state = "error";
      setText("connectionStateText", "Status unavailable");
    }
  }
}

async function refreshInterfaces() {
  try {
    renderInterfaces(await apiRequest("/interfaces"));
  } catch (error) {
    const container = byId("networkInterfaces");
    if (container) container.replaceChildren(textElement("div", "empty", error.message));
    if (!uiState.status) setNotice("connectionApiStatus", error.message, "error");
  }
}

export async function refreshConnectionCenter() {
  setNotice("connectionApiStatus", "Refreshing network status…");
  // The credential check is deliberately outside the all-rejected test below:
  // it is a different service, and its failure should not claim the whole
  // Connection Center is unavailable.
  void refreshCredentialStatus();
  const results = await Promise.allSettled([refreshInterfaces(), refreshStatus(), refreshForwarders()]);
  if (results.every((result) => result.status === "rejected")) {
    setNotice("connectionApiStatus", "Connection services are unavailable. Live, Review, and Setup remain available.", "error");
  }
}

async function copyText(value, description) {
  if (!value || value === "Unavailable") return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const input = document.createElement("textarea");
      input.value = value;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    setNotice("connectionApiStatus", `${description} copied.`, "success");
  } catch {
    setNotice("connectionApiStatus", `Could not copy ${description.toLowerCase()}; select the value manually.`, "error");
  }
}

async function submitListener(event) {
  event.preventDefault();
  const bindHost = byId("listenerBindHost").value.trim();
  const port = Number(byId("listenerPort").value);
  if (!bindHost || !Number.isInteger(port) || port < 1 || port > 65535) {
    setNotice("connectionApiStatus", "Enter a bind address and a UDP port from 1 to 65535.", "error");
    return;
  }
  const action = byId("listenerStart");
  action.disabled = true;
  setNotice("connectionApiStatus", `Starting UDP listener on ${bindHost}:${port}…`);
  try {
    await apiRequest("/listener/start", { method: "POST", body: JSON.stringify({ bind_host: bindHost, port }) });
    await refreshStatus();
  } catch (error) {
    setNotice("connectionApiStatus", error.message, "error");
  } finally {
    action.disabled = false;
  }
}

async function stopListener() {
  const action = byId("listenerStop");
  action.disabled = true;
  setNotice("connectionApiStatus", "Stopping UDP listener…");
  try {
    await apiRequest("/listener/stop", { method: "POST" });
    await refreshStatus();
  } catch (error) {
    setNotice("connectionApiStatus", error.message, "error");
  } finally {
    action.disabled = false;
  }
}

async function submitForwarder(event) {
  event.preventDefault();
  const id = byId("forwarderId").value.trim();
  const label = byId("forwarderLabel").value.trim();
  const host = byId("forwarderHost").value.trim();
  const port = Number(byId("forwarderPort").value);
  if (!label || !host || !Number.isInteger(port) || port < 1 || port > 65535) {
    setNotice("forwarderFormStatus", "Enter a label, host, and port from 1 to 65535.", "error");
    return;
  }
  let packetIds;
  try {
    packetIds = parsePacketIds(byId("forwarderPacketIds").value);
  } catch (error) {
    setNotice("forwarderFormStatus", error.message, "error");
    byId("forwarderPacketIds").focus();
    return;
  }
  const payload = {
    label,
    host,
    port,
    enabled: byId("forwarderEnabled").checked,
    packet_ids: packetIds,
    forward_unknown_packets: byId("forwardUnknownPackets").checked,
    confirm_public_address: byId("forwarderConfirmPublic").checked,
  };
  const submit = byId("saveForwarder");
  submit.disabled = true;
  setNotice("forwarderFormStatus", `${id ? "Saving" : "Adding"} forwarding target…`);
  try {
    await apiRequest(id ? `/forwarders/${encodeURIComponent(id)}` : "/forwarders", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify(id ? payload : { id: undefined, ...payload }),
    });
    const message = id ? `${label} updated.` : `${label} added.`;
    resetForwarderForm();
    await refreshForwarders();
    setNotice("forwarderFormStatus", message, "success");
  } catch (error) {
    setNotice("forwarderFormStatus", error.message, "error");
  } finally {
    submit.disabled = false;
  }
}

function normalizeCheckResult(check) {
  const raw = String(check.status ?? check.result ?? (check.ok === true ? "pass" : check.ok === false ? "fail" : "warning")).toLowerCase();
  if (["pass", "passed", "ok", "healthy", "success"].includes(raw)) return "pass";
  if (["fail", "failed", "error", "unhealthy"].includes(raw)) return "fail";
  return "warning";
}

async function diagnoseNetwork() {
  const action = byId("diagnoseNetwork");
  action.disabled = true;
  setNotice("diagnoseStatus", "Running safe local interface and bind checks…");
  try {
    const report = await apiRequest("/diagnose", { method: "POST" });
    const checksContainer = byId("diagnoseChecks");
    clearChildren(checksContainer);
    (report.checks || []).forEach((check) => {
      const result = normalizeCheckResult(check);
      const card = document.createElement("div");
      card.className = "diagnose-check";
      card.dataset.result = result;
      const label = String(check.label ?? check.name ?? check.code ?? check.id ?? "Network check").replaceAll("_", " ");
      const resultLabel = result === "pass" ? "Passed" : result === "fail" ? "Failed" : "Needs attention";
      card.append(textElement("strong", "", `${resultLabel}: ${label}`));
      const detail = check.message ?? check.detail ?? check.reason;
      if (detail) card.append(textElement("div", "field-help", String(detail)));
      checksContainer.append(card);
    });
    if (!(report.checks || []).length) checksContainer.append(textElement("div", "empty", "No individual diagnostic checks were returned."));
    const actions = byId("diagnoseActions");
    clearChildren(actions);
    (report.actions || []).forEach((item) => actions.append(textElement("li", "", item)));
    if (!(report.actions || []).length) actions.append(textElement("li", "", "No corrective action is currently recommended."));
    setNotice("diagnoseStatus", `Diagnostics completed ${new Date(report.generated_at).toLocaleString()}.`, "success");
  } catch (error) {
    setNotice("diagnoseStatus", error.message, "error");
  } finally {
    action.disabled = false;
  }
}

function stopPolling() {
  if (uiState.pollTimer !== null) window.clearInterval(uiState.pollTimer);
  uiState.pollTimer = null;
}

export function setConnectionCenterActive(active) {
  const nextActive = Boolean(active);
  if (nextActive === uiState.active && (!nextActive || uiState.pollTimer !== null)) return;
  uiState.active = nextActive;
  stopPolling();
  if (!uiState.active) return;
  refreshConnectionCenter();
  uiState.pollTimer = window.setInterval(() => {
    refreshStatus({ quiet: true });
    // Windows adapter discovery spawns PowerShell and can still be warming
    // when this screen first opens. That first answer is the socket-derived
    // fallback, which cannot report adapter kind, gateway or metric. Ask
    // again until the platform answers, so the panel is not stranded on a
    // provisional view until someone presses Refresh.
    if (uiState.interfaces && uiState.interfaces.discovery_authoritative === false) {
      refreshInterfaces();
    }
  }, POLL_INTERVAL_MS);
}

function syncTabs(pageName) {
  const tabs = [...document.querySelectorAll('[role="tab"][data-page]')];
  const knownPage = tabs.some((tab) => tab.dataset.page === pageName) ? pageName : "live";
  tabs.forEach((tab) => {
    const selected = tab.dataset.page === knownPage;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll(".page").forEach((page) => {
    const selected = page.id === knownPage;
    page.classList.toggle("active", selected);
    page.hidden = !selected;
    page.setAttribute("aria-hidden", String(!selected));
    if (!page.hasAttribute("role")) page.setAttribute("role", "tabpanel");
    if (!page.hasAttribute("tabindex")) page.tabIndex = 0;
    const tab = document.querySelector(`[role="tab"][aria-controls="${page.id}"]`);
    if (tab && !page.hasAttribute("aria-labelledby")) page.setAttribute("aria-labelledby", tab.id);
  });
  window.scrollTo(0, 0);
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => window.scrollTo(0, 0)));
  setConnectionCenterActive(knownPage === "connection");
}

function initializeTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"][data-page]')];
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => window.queueMicrotask(() => syncTabs(tab.dataset.page)));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      tabs[nextIndex].focus();
      tabs[nextIndex].click();
    });
  });
  window.addEventListener("hashchange", () => window.queueMicrotask(() => syncTabs(location.hash.slice(1) || "live")));
  window.addEventListener("pitwall:pagechange", (event) => syncTabs(event.detail?.page || "live"));
  syncTabs(location.hash.slice(1) || "live");
}

const CREDENTIAL_BASE = "/api/v1/credentials/openai";

async function credentialRequest(path = "", options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(`${CREDENTIAL_BASE}${path}`, { ...options, headers, credentials: "same-origin" });
  } catch (error) {
    throw new Error(`Could not reach Pit Wall: ${error instanceof Error ? error.message : String(error)}`);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = new Error(apiErrorMessage(payload, `Request failed (${response.status}).`));
    error.status = response.status;
    throw error;
  }
  return payload;
}

export function credentialBadgeState(status) {
  if (!status?.configured) return { state: "off", label: "No key set" };
  if (status.source === "environment") return { state: "listening", label: "Set by environment" };
  return { state: "receiving", label: `Key ${status.masked}` };
}

function renderCredentialStatus(status, message, tone = "info") {
  const { state, label } = credentialBadgeState(status);
  const badge = byId("credentialBadge");
  if (badge) badge.dataset.state = state;
  setText("credentialBadgeText", label, "Unavailable");

  const remove = byId("credentialRemove");
  const test = byId("credentialTest");
  if (remove) remove.disabled = !status?.configured;
  if (test) test.disabled = !status?.configured;

  const detail = message || status?.detail || (status?.configured
    ? "A key is saved. Paste a new one to replace it."
    : "No key saved yet. The engineer radio stays offline until one is set.");
  setNotice("credentialFormStatus", detail, tone);
}

async function refreshCredentialStatus() {
  try {
    const status = await credentialRequest();
    uiState.credentials = status;
    renderCredentialStatus(status, "", status?.source === "environment" ? "warn" : "info");
  } catch (error) {
    uiState.credentials = null;
    setNotice("credentialFormStatus", error.message, "error");
  }
}

function setCredentialBusy(busy) {
  ["credentialSave", "credentialTest", "credentialRemove"].forEach((id) => {
    const element = byId(id);
    if (element) element.disabled = busy;
  });
}

async function submitCredential(event) {
  event.preventDefault();
  const field = byId("credentialKey");
  const apiKey = String(field?.value ?? "").trim();
  if (!apiKey) {
    setNotice("credentialFormStatus", "Paste an API key first.", "error");
    field?.focus();
    return;
  }
  const verify = Boolean(byId("credentialVerify")?.checked);
  setCredentialBusy(true);
  setNotice("credentialFormStatus", verify ? "Checking the key with OpenAI…" : "Saving…");
  try {
    const status = await credentialRequest("", {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey, verify }),
    });
    uiState.credentials = status;
    // Clear the field on success: it is write-only, and leaving the key in the
    // DOM would put it in any screenshot of the Connection Center.
    if (field) field.value = "";
    revealCredential(false);
    renderCredentialStatus(status, "API key saved. The engineer radio is ready.", "success");
  } catch (error) {
    renderCredentialStatus(uiState.credentials, error.message, "error");
  } finally {
    setCredentialBusy(false);
  }
}

async function testCredential() {
  setCredentialBusy(true);
  setNotice("credentialFormStatus", "Asking OpenAI to confirm the saved key…");
  try {
    const result = await credentialRequest("/test", { method: "POST" });
    renderCredentialStatus(uiState.credentials, result?.detail || "", result?.ok ? "success" : "error");
  } catch (error) {
    renderCredentialStatus(uiState.credentials, error.message, "error");
  } finally {
    setCredentialBusy(false);
  }
}

async function removeCredential() {
  if (HAS_DOM && typeof window.confirm === "function") {
    const confirmed = window.confirm("Remove the saved OpenAI API key? The engineer radio will stop answering until a new one is set.");
    if (!confirmed) return;
  }
  setCredentialBusy(true);
  try {
    const status = await credentialRequest("", { method: "DELETE" });
    uiState.credentials = status;
    renderCredentialStatus(status, "API key removed.", "success");
  } catch (error) {
    renderCredentialStatus(uiState.credentials, error.message, "error");
  } finally {
    setCredentialBusy(false);
  }
}

function revealCredential(next) {
  const field = byId("credentialKey");
  const toggle = byId("credentialReveal");
  if (!field || !toggle) return;
  const shown = next === undefined ? field.type === "password" : Boolean(next);
  field.type = shown ? "text" : "password";
  toggle.textContent = shown ? "Hide" : "Show";
  toggle.setAttribute("aria-pressed", String(shown));
}

function bindEvents() {
  byId("credentialForm")?.addEventListener("submit", submitCredential);
  byId("credentialTest")?.addEventListener("click", testCredential);
  byId("credentialRemove")?.addEventListener("click", removeCredential);
  byId("credentialReveal")?.addEventListener("click", () => revealCredential());
  byId("listenerForm")?.addEventListener("submit", submitListener);
  byId("listenerStop")?.addEventListener("click", stopListener);
  byId("refreshNetwork")?.addEventListener("click", refreshConnectionCenter);
  byId("copyRecommendedIp")?.addEventListener("click", () => copyText(byId("recommendedIpv4").textContent, "PS5 UDP IP address"));
  byId("copyRecommendedPort")?.addEventListener("click", () => copyText(byId("recommendedPort").textContent, "UDP port"));
  byId("forwarderForm")?.addEventListener("submit", submitForwarder);
  byId("cancelForwarderEdit")?.addEventListener("click", resetForwarderForm);
  byId("diagnoseNetwork")?.addEventListener("click", diagnoseNetwork);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else if (uiState.active) setConnectionCenterActive(true);
  });
}

function initialize() {
  if (!byId("connection")) return;
  const canvasLabels = {
    paceSpark: "Recent lap pace trend",
    trace: "Live lap timing trace",
    lineMap: "Racing line and lap map",
  };
  Object.entries(canvasLabels).forEach(([id, label]) => {
    const canvas = byId(id);
    if (!canvas) return;
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", label);
  });
  bindEvents();
  initializeTabs();
  window.pitwallConnection = {
    refresh: refreshConnectionCenter,
    activate: () => setConnectionCenterActive(true),
    deactivate: () => setConnectionCenterActive(false),
  };
}

if (HAS_DOM) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
}
