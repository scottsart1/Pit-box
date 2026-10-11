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
    off: i18n.t("Off"),
    listening: i18n.t("Listening — waiting for telemetry"),
    receiving: i18n.t("Receiving telemetry"),
    stale: i18n.t("Telemetry stale"),
    error: i18n.t("Listener error"),
  }[value] || i18n.t("Unavailable");
}

export function parsePacketIds(value) {
  const source = String(value ?? "").trim();
  if (!source || source.toLowerCase() === "all") return "all";
  const parts = source.split(",").map((part) => part.trim()).filter(Boolean);
  if (!parts.length) return "all";
  const packetIds = [...new Set(parts.map((part) => Number(part)))].sort((a, b) => a - b);
  if (packetIds.some((packetId) => !Number.isInteger(packetId) || packetId < 0 || packetId > 255)) {
    throw new Error(i18n.t("Packet IDs must be whole numbers from 0 to 255, or ‘all’."));
  }
  return packetIds;
}

export function formatAge(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(Number(milliseconds))) return i18n.t("Unavailable");
  const value = Math.max(0, Number(milliseconds));
  if (value < 1000) return i18n.t("{ms} ms ago", { ms: Math.round(value) });
  if (value < 60000) return i18n.t("{seconds} s ago", { seconds: (value / 1000).toFixed(value < 10000 ? 1 : 0) });
  return i18n.t("{minutes} min ago", { minutes: Math.floor(value / 60000) });
}

export function formatCount(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return i18n.t("Unavailable");
  return new Intl.NumberFormat().format(Number(value));
}

export function apiErrorMessage(payload, fallback = i18n.t("The request could not be completed.")) {
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
    throw new Error(i18n.t("Connection service is unreachable: {error}", { error: error instanceof Error ? error.message : String(error) }));
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
      ? i18n.t("Connection services are unavailable in this build. Live, Review, and Setup remain available.")
      : i18n.t("Network request failed ({status}).", { status: response.status });
    const error = new Error(apiErrorMessage(payload, unavailable));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setText(id, value, fallback = i18n.t("Unavailable")) {
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
  if (!source) return i18n.t("Unavailable");
  const host = source.ip ?? source.host ?? source.address ?? source.source_ip;
  const port = source.port ?? source.source_port;
  if (!host) return i18n.t("Unavailable");
  return port === null || port === undefined ? String(host) : `${host}:${port}`;
}

function renderWarnings(warnings = []) {
  const container = byId("networkWarnings");
  if (!container) return;
  clearChildren(container);
  const unique = [...new Set(warnings.filter((item) => typeof item === "string" && item.trim()))];
  if (!unique.length) return;
  const title = textElement("strong", "", i18n.tp(unique.length, "Connection warning", "Connection warnings"));
  const list = document.createElement("ul");
  unique.forEach((warning) => list.append(textElement("li", "", warning)));
  container.append(title, list);
}

// Packet states are server codes; the known ones are shown translated,
// anything else as sent.
function packetStateLabel(state) {
  const labels = {
    healthy: i18n.t("Healthy"),
    stale: i18n.t("stale"),
    degraded: i18n.t("degraded"),
    provisional_gap: i18n.t("provisional gap"),
    unavailable: i18n.t("unavailable"),
  };
  return Object.prototype.hasOwnProperty.call(labels, state) ? labels[state] : state.replaceAll("_", " ");
}

function renderPacketHealth(packets = []) {
  const body = byId("packetHealthBody");
  if (!body) return;
  clearChildren(body);
  if (!packets.length) {
    const row = document.createElement("tr");
    const cell = textElement("td", "empty", i18n.t("No packet data available. Listening alone does not prove that the game is sending telemetry."));
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
    return;
  }
  [...packets].sort((a, b) => Number(a.packet_id) - Number(b.packet_id)).forEach((packet) => {
    const row = document.createElement("tr");
    const packetName = packet.packet_name ? String(packet.packet_name).replaceAll("_", " ") : i18n.t("packet {id}", { id: `${packet.packet_id}` });
    const state = String(packet.status || "unavailable").toLowerCase();
    const stateCell = document.createElement("td");
    const chip = textElement("span", "state-chip", packetStateLabel(state));
    chip.dataset.state = state === "healthy" ? "healthy" : state.includes("error") || state.includes("invalid") ? "error" : "warning";
    stateCell.append(chip);
    const hz = Number.isFinite(Number(packet.observed_hz_10s)) ? `${Number(packet.observed_hz_10s).toFixed(1)} Hz` : i18n.t("Unavailable");
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

export function packetReceptionSummary(payload) {
  const packets = payload?.packets || [];
  const counters = payload?.datagrams || {};
  const received = counters.received ?? packets.reduce((count, packet) => count + Number(packet.received || 0), 0);
  const parsed = counters.parsed ?? packets.reduce((count, packet) => count + Number(packet.valid || 0), 0);
  const rejected = counters.rejected ?? packets.reduce((count, packet) => count + Number(packet.invalid || 0), 0);
  const unsupported = (payload?.invalid_packets || []).some((packet) => Number(packet.unsupported_format) > 0);
  let message = "";
  if (received > 0 && parsed === 0) {
    message = unsupported
      ? i18n.t("UDP packets reached this device, but their game format is unsupported. Select UDP format 2026 in the game (F1 25 + 2026 Season Pack).")
      : i18n.t("UDP packets reached this device, but none parsed successfully. Check the game’s UDP format is 2026 and that another app is not sending unrelated traffic to this port.");
  } else if (parsed > 0 && rejected > 0) {
    message = i18n.t("Some received packets were rejected. The counters show traffic observed so far; the receiver badge above shows whether valid telemetry is arriving now.");
  }
  return { received, parsed, rejected, message };
}

function renderReceptionCounters(payload) {
  const summary = packetReceptionSummary(payload);
  setText("networkDatagrams", formatCount(summary.received));
  setText("networkParsed", formatCount(summary.parsed));
  setText("networkRejected", formatCount(summary.rejected));
  const notice = byId("networkParseNotice");
  if (notice) { notice.hidden = !summary.message; setNotice("networkParseNotice", summary.message, summary.parsed ? "info" : "error"); }
}

async function refreshAndroidStatus() {
  const notice = byId("androidConnectionStatus");
  if (!notice) return;
  try {
    const payload = await apiRequest("/android");
    if (!payload?.available) {
      notice.hidden = payload?.platform !== "android";
      if (!notice.hidden) setNotice("androidConnectionStatus", i18n.t("Android network diagnostics are unavailable. Use the device address and packet counters above to check reception."));
      return;
    }
    notice.hidden = false;
    const networks = (payload.networks || []).filter((network) => ["wifi", "ethernet"].includes(network.transport));
    const addresses = networks.flatMap((network) => (network.addresses || []).map((address) => address.address)).filter((address) => typeof address === "string" && /^\d+\.\d+\.\d+\.\d+$/.test(address));
    const parts = [payload.service_running ? i18n.t("Android receiver service is running.") : i18n.t("Android receiver service is not running. Reopen the app to start it.")];
    if (addresses.length) parts.push(i18n.tp(addresses.length, "Local address: {addresses}.", "Local addresses: {addresses}.", { addresses: [...new Set(addresses)].join(", ") }));
    else parts.push(i18n.t("Check the network adapter address below; a Wi-Fi or Ethernet connection is needed to receive from the game."));
    if (payload.internet_permission === false) parts.push(i18n.t("This installation is missing network permission."));
    if (Number.isInteger(payload.network_losses)) parts.push(i18n.tp(payload.network_losses, "Android reported {count} network loss event since the receiver started.", "Android reported {count} network loss events since the receiver started.", { count: payload.network_losses }));
    if (Number(payload.thermal_status) >= 3) parts.push(i18n.t("Android reports thermal throttling; processing may slow while the device cools."));
    if (Array.isArray(payload.warnings)) parts.push(...payload.warnings);
    setNotice("androidConnectionStatus", parts.join(" "), payload.service_running && payload.internet_permission !== false ? "info" : "error");
  } catch {
    // Desktop and older builds may not provide native diagnostics. Their
    // ordinary listener/packet status remains independently useful.
    notice.hidden = true;
  }
}

// The recommendation's confidence is a server code: high, medium, low or none.
function confidenceLabel(level) {
  const labels = {
    high: i18n.t("high confidence"),
    medium: i18n.t("medium confidence"),
    low: i18n.t("low confidence"),
    none: i18n.t("none confidence"),
  };
  return Object.prototype.hasOwnProperty.call(labels, level) ? labels[level] : i18n.t("{level} confidence", { level: `${level}` });
}

function renderStatus(payload) {
  uiState.status = payload;
  renderReceptionCounters(payload);
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
  const confidence = recommendation?.confidence ? ` · ${confidenceLabel(recommendation.confidence)}` : "";
  setText("recommendedAdapter", interfaceMatch ? `${interfaceMatch.name}${confidence}` : recommendation?.adapter_id ? `${recommendation.adapter_id}${confidence}` : i18n.t("No adapter recommendation yet."));

  if (document.activeElement !== byId("listenerBindHost")) setControlValue("listenerBindHost", listener.bind_host || "0.0.0.0", "0.0.0.0");
  if (document.activeElement !== byId("listenerPort")) setControlValue("listenerPort", listener.port ?? 20777, "20777");
  setText("networkSource", formatEndpoint(payload?.source));
  setText("networkFormat", payload?.game?.packet_format ?? payload?.game?.format);
  setText("networkSessionUid", payload?.game?.session_uid);
  setText("networkLastPacket", formatAge(listener.last_valid_packet_age_ms));

  const startButton = byId("listenerStart");
  const stopButton = byId("listenerStop");
  if (startButton) startButton.textContent = listenerState === "off" || listenerState === "error" ? i18n.t("Start listening") : i18n.t("Restart listener");
  if (stopButton) stopButton.disabled = listenerState === "off";

  const listenerMessages = {
    off: i18n.t("Listener is off. Start it when you are ready to receive telemetry."),
    listening: i18n.t("Listening successfully, but no recent valid F1 telemetry has arrived yet."),
    receiving: i18n.t("Valid F1 telemetry is arriving from the game."),
    stale: i18n.t("Telemetry arrived previously but is now older than its freshness budget."),
    error: listener.error || i18n.t("The UDP listener reported an error."),
  };
  const tone = listenerState === "receiving" ? "success" : listenerState === "error" ? "error" : "info";
  setNotice("connectionApiStatus", listenerMessages[listenerState] || i18n.t("Connection state unavailable."), tone);
  renderWarnings([...(payload?.warnings || []), ...(uiState.interfaces?.warnings || [])]);
  renderPacketHealth(payload?.packets || []);
  if (Array.isArray(payload?.forwarders)) {
    uiState.forwarders = payload.forwarders;
    renderForwarders(uiState.forwarders);
  }
}

// Adapter kinds are server codes. ethernet, wifi and vpn are shown as sent;
// the descriptive ones are translated.
function adapterKindLabel(kind) {
  if (!kind) return i18n.t("adapter");
  const labels = {
    virtual: i18n.t("virtual"),
    cellular: i18n.t("cellular"),
    loopback: i18n.t("loopback"),
    unknown: i18n.t("unknown"),
  };
  return Object.prototype.hasOwnProperty.call(labels, kind) ? labels[kind] : String(kind);
}

function renderInterfaces(payload) {
  uiState.interfaces = payload;
  const container = byId("networkInterfaces");
  if (!container) return;
  clearChildren(container);
  const interfaces = [...(payload?.interfaces || [])].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  if (!interfaces.length) {
    container.append(textElement("div", "empty", i18n.t("No usable IPv4 adapters were reported. Check that Wi-Fi or Ethernet is connected.")));
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
    identity.append(textElement("strong", "", item.name || item.description || i18n.t("Network adapter")));
    /* The address is what the user copies into the PS5 and into the bind
       field. Rendering it as "10.234.197.105/24" put a slash-number right
       where a port is expected — a real user read the CIDR prefix as the
       port, started the listener on port 24, and lost telemetry at every
       launch for three days. The prefix is network trivia; label it as such,
       away from the address. */
    identity.append(textElement("div", "adapter-address", item.ipv4 ? item.ipv4 : i18n.t("IPv4 unavailable")));
    if (item.ipv4 && item.prefix_length !== null && item.prefix_length !== undefined) {
      identity.append(textElement("div", "field-help", i18n.t("subnet mask /{prefix} — not a port", { prefix: item.prefix_length })));
    }
    const stateText = item.operational ? i18n.t("Active") : i18n.t("Inactive");
    const stateChip = textElement("span", "state-chip", stateText);
    stateChip.dataset.state = item.operational ? "healthy" : "warning";
    header.append(identity, stateChip);
    card.append(header);
    if (item.description && item.description !== item.name) card.append(textElement("div", "field-help", item.description));
    const tags = document.createElement("div");
    tags.className = "tag-row";
    if (recommended) tags.append(textElement("span", "tag-chip recommended", i18n.t("Recommended for PS5 / second PC")));
    if (item.pinned) tags.append(textElement("span", "tag-chip pinned", i18n.t("Pinned choice")));
    if (item.previously_worked) tags.append(textElement("span", "tag-chip", i18n.t("Valid F1 traffic seen before")));
    if (item.default_gateway) tags.append(textElement("span", "tag-chip", i18n.t("Default route")));
    tags.append(textElement("span", "tag-chip", adapterKindLabel(item.kind)));
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
  setText("recommendedAdapter", recommended ? (recommended.pinned ? i18n.t("{name} · pinned", { name: `${recommended.name}` }) : `${recommended.name}`) : i18n.t("No adapter recommendation yet."));
  renderWarnings([...(payload.warnings || []), ...(uiState.status?.warnings || [])]);
}

function targetPacketLabel(packetIds) {
  return packetIds === "all" ? i18n.t("All known packets") : i18n.t("Packet IDs {ids}", { ids: packetIds.join(", ") });
}

function renderForwarders(targets = []) {
  const container = byId("forwarderList");
  if (!container) return;
  clearChildren(container);
  if (!targets.length) {
    container.append(textElement("div", "empty", i18n.t("No forwarding targets configured. Local telemetry ingestion is unaffected.")));
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
    if (target.resolved_address && target.resolved_address !== target.host) identity.append(textElement("span", "field-help", i18n.t("Resolves to {address}", { address: target.resolved_address })));
    const status = textElement("span", "state-chip", target.enabled ? i18n.t("Enabled") : i18n.t("Disabled"));
    status.dataset.state = target.enabled ? (target.last_error ? "error" : "healthy") : "warning";
    head.append(identity, status);
    card.append(head, textElement("div", "field-help", target.forward_unknown_packets ? i18n.t("{packets} · unknown packets included", { packets: targetPacketLabel(target.packet_ids) }) : targetPacketLabel(target.packet_ids)));
    const counters = document.createElement("div");
    counters.className = "counter-grid";
    [
      [i18n.t("Packets sent"), formatCount(target.packets_sent)],
      [i18n.t("Bytes sent"), formatCount(target.bytes_sent)],
      [i18n.t("Queue drops"), formatCount(target.queue_drops)],
      [i18n.t("Socket errors"), formatCount(target.socket_errors)],
    ].forEach(([label, value]) => {
      const item = document.createElement("div");
      item.append(textElement("span", "", label), textElement("strong", "", value));
      counters.append(item);
    });
    card.append(counters);
    if (target.last_success) card.append(textElement("div", "field-help", i18n.t("Last successful send: {time}", { time: new Date(target.last_success).toLocaleString() })));
    if (target.last_error) card.append(textElement("div", "error small", i18n.t("Latest socket error: {error}", { error: target.last_error })));
    const actions = document.createElement("div");
    actions.className = "forwarder-actions";
    actions.append(
      button(i18n.t("Edit"), i18n.t("Edit {label}", { label: `${target.label}` }), () => editForwarder(target)),
      button(target.enabled ? i18n.t("Disable") : i18n.t("Enable"), target.enabled ? i18n.t("Disable {label}", { label: `${target.label}` }) : i18n.t("Enable {label}", { label: `${target.label}` }), () => toggleForwarder(target)),
      button(i18n.t("Delete"), i18n.t("Delete {label}", { label: `${target.label}` }), () => deleteForwarder(target), "button ghost danger-button"),
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
  byId("saveForwarder").textContent = i18n.t("Add target");
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
  byId("saveForwarder").textContent = i18n.t("Save target");
  byId("cancelForwarderEdit").hidden = false;
  setNotice("forwarderFormStatus", i18n.t("Editing {label}. Public destinations must be reconfirmed when changed.", { label: `${target.label}` }));
  byId("forwarderLabel").focus();
}

async function toggleForwarder(target) {
  setNotice("forwarderFormStatus", target.enabled ? i18n.t("Disabling {label}…", { label: `${target.label}` }) : i18n.t("Enabling {label}…", { label: `${target.label}` }));
  try {
    await apiRequest(`/forwarders/${encodeURIComponent(target.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !target.enabled }),
    });
    await refreshForwarders();
    setNotice("forwarderFormStatus", target.enabled ? i18n.t("{label} disabled.", { label: `${target.label}` }) : i18n.t("{label} enabled.", { label: `${target.label}` }), "success");
  } catch (error) {
    setNotice("forwarderFormStatus", error.message, "error");
  }
}

async function deleteForwarder(target) {
  if (!window.confirm(i18n.t("Delete forwarding target “{label}” ({host}:{port})?", { label: `${target.label}`, host: `${target.host}`, port: `${target.port}` }))) return;
  setNotice("forwarderFormStatus", i18n.t("Deleting {label}…", { label: `${target.label}` }));
  try {
    await apiRequest(`/forwarders/${encodeURIComponent(target.id)}`, { method: "DELETE" });
    if (byId("forwarderId").value === target.id) resetForwarderForm();
    await refreshForwarders();
    setNotice("forwarderFormStatus", i18n.t("{label} deleted.", { label: `${target.label}` }), "success");
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
      setText("connectionStateText", i18n.t("Status unavailable"));
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
  setNotice("connectionApiStatus", i18n.t("Refreshing network status…"));
  // The credential check is deliberately outside the all-rejected test below:
  // it is a different service, and its failure should not claim the whole
  // Connection Center is unavailable.
  void refreshCredentialStatus();
  void refreshAndroidStatus();
  const results = await Promise.allSettled([refreshInterfaces(), refreshStatus(), refreshForwarders()]);
  if (results.every((result) => result.status === "rejected")) {
    setNotice("connectionApiStatus", i18n.t("Connection services are unavailable. Live, Review, and Setup remain available."), "error");
  }
}

async function copyText(value, copiedMessage, failedMessage) {
  // The value is read from the page, where an empty field shows the
  // "Unavailable" placeholder in the screen language.
  if (!value || value === "Unavailable" || value === i18n.t("Unavailable")) return;
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
    setNotice("connectionApiStatus", copiedMessage, "success");
  } catch {
    setNotice("connectionApiStatus", failedMessage, "error");
  }
}

async function submitListener(event) {
  event.preventDefault();
  const bindHost = byId("listenerBindHost").value.trim();
  const port = Number(byId("listenerPort").value);
  if (!bindHost || !Number.isInteger(port) || port < 1 || port > 65535) {
    setNotice("connectionApiStatus", i18n.t("Enter a bind address and a UDP port from 1 to 65535."), "error");
    return;
  }
  const action = byId("listenerStart");
  action.disabled = true;
  setNotice("connectionApiStatus", i18n.t("Starting UDP listener on {host}:{port}…", { host: bindHost, port }));
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
  setNotice("connectionApiStatus", i18n.t("Stopping UDP listener…"));
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
    setNotice("forwarderFormStatus", i18n.t("Enter a label, host, and port from 1 to 65535."), "error");
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
  setNotice("forwarderFormStatus", id ? i18n.t("Saving forwarding target…") : i18n.t("Adding forwarding target…"));
  try {
    await apiRequest(id ? `/forwarders/${encodeURIComponent(id)}` : "/forwarders", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify(id ? payload : { id: undefined, ...payload }),
    });
    const message = id ? i18n.t("{label} updated.", { label }) : i18n.t("{label} added.", { label });
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
  setNotice("diagnoseStatus", i18n.t("Running safe local interface and bind checks…"));
  try {
    const report = await apiRequest("/diagnose", { method: "POST" });
    const checksContainer = byId("diagnoseChecks");
    clearChildren(checksContainer);
    (report.checks || []).forEach((check) => {
      const result = normalizeCheckResult(check);
      const card = document.createElement("div");
      card.className = "diagnose-check";
      card.dataset.result = result;
      const label = String(check.label ?? check.name ?? check.code ?? check.id ?? i18n.t("Network check")).replaceAll("_", " ");
      const heading = result === "pass" ? i18n.t("Passed: {check}", { check: label }) : result === "fail" ? i18n.t("Failed: {check}", { check: label }) : i18n.t("Needs attention: {check}", { check: label });
      card.append(textElement("strong", "", heading));
      const detail = check.message ?? check.detail ?? check.reason;
      if (detail) card.append(textElement("div", "field-help", String(detail)));
      checksContainer.append(card);
    });
    if (!(report.checks || []).length) checksContainer.append(textElement("div", "empty", i18n.t("No individual diagnostic checks were returned.")));
    const actions = byId("diagnoseActions");
    clearChildren(actions);
    (report.actions || []).forEach((item) => actions.append(textElement("li", "", item)));
    if (!(report.actions || []).length) actions.append(textElement("li", "", i18n.t("No corrective action is currently recommended.")));
    setNotice("diagnoseStatus", i18n.t("Diagnostics completed {time}.", { time: new Date(report.generated_at).toLocaleString() }), "success");
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
    refreshAndroidStatus();
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

// The ANALYSIS page hosts sub-views with their own role="tab" buttons.
// syncTabs must treat a sub-view name as "the analysis page" and must never
// collect the sub-tab buttons themselves: matching them made knownPage a
// sub-view id, no <main> matched it, and every page went hidden at once —
// the blank-screen-with-status-rail bug (2026-08-12).
const ANALYSIS_SUBVIEWS = ["test-engineer", "library", "session-analysis", "session-review", "lap-lab", "field", "review"];

function syncTabs(pageName) {
  if (ANALYSIS_SUBVIEWS.includes(pageName)) pageName = "analysis";
  const tabs = [...document.querySelectorAll('.tab[role="tab"][data-page]')];
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
  // Top-level tabs only — the analysis sub-tabs have their own handlers and
  // roving them together with the top nav made ArrowRight jump between rows.
  const tabs = [...document.querySelectorAll('.tab[role="tab"][data-page]')];
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

const CREDENTIAL_BASE = "/api/v1/credentials";

// Labels and paste hints per engine provider. Mirrors the backend registry;
// unknown ids degrade to their raw name rather than breaking the panel.
// Built when used, in the screen language, so loading the module needs no
// translation runtime.
function providerMeta(provider) {
  return {
    openai: { label: "OpenAI", hint: i18n.t("Starts with {prefix}. Paste the whole value.", { prefix: "sk-" }), placeholder: "sk-…" },
    anthropic: { label: "Anthropic (Claude)", hint: i18n.t("Starts with {prefix}. Paste the whole value.", { prefix: "sk-ant-" }), placeholder: "sk-ant-…" },
    deepseek: { label: "DeepSeek", hint: i18n.t("From {site}. Paste the whole value.", { site: "platform.deepseek.com" }), placeholder: "sk-…" },
    kimi: { label: "Kimi (Moonshot AI)", hint: i18n.t("From {site}. Paste the whole value.", { site: "platform.moonshot.ai" }), placeholder: "sk-…" },
    custom: { label: i18n.t("Custom endpoint"), hint: i18n.t("Whatever token your OpenAI-compatible endpoint expects (optional for local servers)."), placeholder: i18n.t("token…") },
  }[provider];
}

function providerLabel(provider) {
  return providerMeta(provider)?.label || provider;
}

function selectedCredentialProvider() {
  return byId("credentialProvider")?.value || "openai";
}

async function credentialRequest(path = "", options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(`${CREDENTIAL_BASE}${path}`, { ...options, headers, credentials: "same-origin" });
  } catch (error) {
    throw new Error(i18n.t("Could not reach Pit Wall: {error}", { error: error instanceof Error ? error.message : String(error) }));
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = new Error(apiErrorMessage(payload, i18n.t("Request failed ({status}).", { status: response.status })));
    error.status = response.status;
    throw error;
  }
  return payload;
}

export function credentialBadgeState(status) {
  if (!status?.configured) return { state: "off", label: i18n.t("No key set") };
  if (status.source === "environment") return { state: "listening", label: i18n.t("Set by environment") };
  return { state: "receiving", label: i18n.t("Key {masked}", { masked: `${status.masked}` }) };
}

function renderProviderOptions(overview) {
  const select = byId("credentialProvider");
  if (!select) return;
  for (const option of select.options) {
    const meta = providerLabel(option.value);
    const entry = overview?.providers?.[option.value];
    option.textContent = entry?.configured ? i18n.t("{provider} ✓ key saved", { provider: meta }) : meta;
  }
  const active = byId("activeProviderSelect");
  if (active && overview?.active_provider && active.value !== overview.active_provider) {
    active.value = overview.active_provider;
  }
}

function renderCredentialStatus(message, tone = "info") {
  const provider = selectedCredentialProvider();
  const overview = uiState.credentials;
  const status = overview?.providers?.[provider] || null;
  const { state, label } = credentialBadgeState(status);
  const badge = byId("credentialBadge");
  if (badge) badge.dataset.state = state;
  setText("credentialBadgeText", `${providerLabel(provider)}: ${label}`, i18n.t("Unavailable"));

  const meta = providerMeta(provider) || {};
  setText("credentialKeyHint", meta.hint || i18n.t("Paste the whole value."));
  setText("credentialVerifyLabel", i18n.t("Check the key with {provider} before saving", { provider: providerLabel(provider) }));
  const field = byId("credentialKey");
  if (field && meta.placeholder) field.placeholder = meta.placeholder;

  const remove = byId("credentialRemove");
  const test = byId("credentialTest");
  if (remove) remove.disabled = !status?.configured;
  if (test) test.disabled = !status?.configured;

  let detail = message;
  if (!detail) {
    detail = status?.configured
      ? i18n.t("A {provider} key is saved. Paste a new one to replace it.", { provider: providerLabel(provider) })
      : i18n.t("No {provider} key saved yet.", { provider: providerLabel(provider) });
    if (overview && !overview.voice_ready) {
      detail += " " + i18n.t("Voice needs an OpenAI key; other providers answer in text only.");
    }
  }
  setNotice("credentialFormStatus", detail, tone);
}

async function refreshCredentialStatus() {
  try {
    const overview = await credentialRequest();
    uiState.credentials = overview;
    renderProviderOptions(overview);
    const provider = selectedCredentialProvider();
    const source = overview?.providers?.[provider]?.source;
    renderCredentialStatus("", source === "environment" ? "warn" : "info");
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
  const provider = selectedCredentialProvider();
  const field = byId("credentialKey");
  const apiKey = String(field?.value ?? "").trim();
  if (!apiKey) {
    setNotice("credentialFormStatus", i18n.t("Paste an API key first."), "error");
    field?.focus();
    return;
  }
  const verify = Boolean(byId("credentialVerify")?.checked);
  setCredentialBusy(true);
  setNotice("credentialFormStatus", verify ? i18n.t("Checking the key with {provider}…", { provider: providerLabel(provider) }) : i18n.t("Saving…"));
  try {
    await credentialRequest(`/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey, verify }),
    });
    // Clear the field on success: it is write-only, and leaving the key in the
    // DOM would put it in any screenshot of the Connection Center.
    if (field) field.value = "";
    revealCredential(false);
    await refreshCredentialStatus();
    renderCredentialStatus(i18n.t("{provider} key saved. The engineer can use it now.", { provider: providerLabel(provider) }), "success");
  } catch (error) {
    renderCredentialStatus(error.message, "error");
  } finally {
    setCredentialBusy(false);
  }
}

async function testCredential() {
  const provider = selectedCredentialProvider();
  setCredentialBusy(true);
  setNotice("credentialFormStatus", i18n.t("Asking {provider} to confirm the saved key…", { provider: providerLabel(provider) }));
  try {
    const result = await credentialRequest(`/${provider}/test`, { method: "POST" });
    renderCredentialStatus(result?.detail || "", result?.ok ? "success" : "error");
  } catch (error) {
    renderCredentialStatus(error.message, "error");
  } finally {
    setCredentialBusy(false);
  }
}

async function removeCredential() {
  const provider = selectedCredentialProvider();
  if (HAS_DOM && typeof window.confirm === "function") {
    const confirmed = window.confirm(i18n.t("Remove the saved {provider} API key? The engineer cannot use {provider} until a new one is set.", { provider: providerLabel(provider) }));
    if (!confirmed) return;
  }
  setCredentialBusy(true);
  try {
    await credentialRequest(`/${provider}`, { method: "DELETE" });
    await refreshCredentialStatus();
    renderCredentialStatus(i18n.t("{provider} key removed.", { provider: providerLabel(provider) }), "success");
  } catch (error) {
    renderCredentialStatus(error.message, "error");
  } finally {
    setCredentialBusy(false);
  }
}

async function changeActiveProvider() {
  const select = byId("activeProviderSelect");
  if (!select) return;
  const provider = select.value;
  setNotice("credentialFormStatus", provider === "auto" ? i18n.t("Switching the engineer to automatic selection…") : i18n.t("Switching the engineer to {provider}…", { provider: providerLabel(provider) }));
  try {
    const response = await fetch("/api/v1/app-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ llm_provider: provider }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(apiErrorMessage(payload, i18n.t("Request failed ({status}).", { status: response.status })));
    await refreshCredentialStatus();
    const overview = uiState.credentials;
    const configured = overview?.providers?.[provider]?.configured;
    if (provider !== "auto" && provider !== "custom" && overview && configured === false) {
      renderCredentialStatus(i18n.t("Engineer set to {provider} — now save a {provider} key below or the radio has nothing to call.", { provider: providerLabel(provider) }), "warn");
    } else {
      renderCredentialStatus(provider === "auto" ? i18n.t("Engineer now runs on the first configured provider.") : i18n.t("Engineer now runs on {provider}.", { provider: providerLabel(provider) }), "success");
    }
  } catch (error) {
    renderCredentialStatus(error.message, "error");
  }
}

function revealCredential(next) {
  const field = byId("credentialKey");
  const toggle = byId("credentialReveal");
  if (!field || !toggle) return;
  const shown = next === undefined ? field.type === "password" : Boolean(next);
  field.type = shown ? "text" : "password";
  toggle.textContent = shown ? i18n.t("Hide") : i18n.t("Show");
  toggle.setAttribute("aria-pressed", String(shown));
}

function bindEvents() {
  byId("credentialForm")?.addEventListener("submit", submitCredential);
  byId("credentialTest")?.addEventListener("click", testCredential);
  byId("credentialRemove")?.addEventListener("click", removeCredential);
  byId("credentialReveal")?.addEventListener("click", () => revealCredential());
  byId("credentialProvider")?.addEventListener("change", () => {
    const field = byId("credentialKey");
    if (field) field.value = "";
    revealCredential(false);
    renderCredentialStatus("");
  });
  byId("activeProviderSelect")?.addEventListener("change", changeActiveProvider);
  byId("listenerForm")?.addEventListener("submit", submitListener);
  byId("listenerStop")?.addEventListener("click", stopListener);
  byId("refreshNetwork")?.addEventListener("click", refreshConnectionCenter);
  byId("copyRecommendedIp")?.addEventListener("click", () => copyText(byId("recommendedIpv4").textContent, i18n.t("PS5 / second PC UDP IP address copied."), i18n.t("Could not copy ps5 / second pc udp ip address; select the value manually.")));
  byId("copyRecommendedPort")?.addEventListener("click", () => copyText(byId("recommendedPort").textContent, i18n.t("UDP port copied."), i18n.t("Could not copy udp port; select the value manually.")));
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
    paceSpark: i18n.t("Recent lap pace trend"),
    trace: i18n.t("Live lap timing trace"),
    lineMap: i18n.t("Racing line and lap map"),
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
