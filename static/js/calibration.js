/* CONNECTION · Circuit model: what recorded races have measured at each
   circuit (pit loss, tyre severity, safety-car rate), beside the built-in
   tables, with the source the strategy actually uses. Calibration is pit-lane
   admin, so it lives here rather than on STRATEGY. */

const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";
const byId = (id) => document.getElementById(id);

function formatValue(name, value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  if (name === "pit_loss_s") return `${number.toFixed(1)} s`;
  if (name === "safety_car_per_lap") return number.toFixed(3);
  return number.toFixed(2);
}

function circuitCard(circuit, onForget) {
  const card = document.createElement("article");
  card.className = "circuit-model-card";
  const head = document.createElement("div");
  head.className = "circuit-model-head";
  const title = document.createElement("h3");
  title.textContent = circuit.current
    ? i18n.t("{track} · current circuit", { track: circuit.track_name })
    : String(circuit.track_name);
  head.appendChild(title);
  if (circuit.has_data) {
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "button ghost";
    forget.textContent = i18n.t("Forget this circuit");
    forget.addEventListener("click", () => onForget(circuit));
    head.appendChild(forget);
  }
  card.appendChild(head);
  const table = document.createElement("table");
  table.className = "table";
  const caption = document.createElement("caption");
  caption.className = "sr-only";
  caption.textContent = i18n.t("Measured and table values for {track}", { track: circuit.track_name });
  table.appendChild(caption);
  const body = document.createElement("tbody");
  for (const row of circuit.quantities || []) {
    const line = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = i18n.t(row.label);
    const value = document.createElement("td");
    value.textContent = formatValue(row.name, row.value);
    const source = document.createElement("td");
    source.className = "small muted";
    source.textContent = row.source;
    line.append(label, value, source);
    body.appendChild(line);
  }
  table.appendChild(body);
  card.appendChild(table);
  return card;
}

async function loadCircuitModel() {
  const status = byId("circuitModelStatus");
  const list = byId("circuitModelList");
  if (!status || !list) return;
  try {
    const response = await fetch("/api/circuit-model", { credentials: "same-origin" });
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    const circuits = Array.isArray(payload.circuits) ? payload.circuits : [];
    list.replaceChildren(...circuits.map((circuit) => circuitCard(circuit, forget)));
    status.textContent = circuits.some((circuit) => circuit.has_data)
      ? i18n.t("Measurements update at the end of every race.")
      : i18n.t("No race recorded yet. Measurements start with the first finished race.");
  } catch {
    status.textContent = i18n.t("The circuit model is unavailable.");
  }
}

async function forget(circuit) {
  if (!window.confirm(i18n.t("Forget what recorded races measured at {track}?", { track: circuit.track_name }))) return;
  await fetch(`/api/circuit-model/${encodeURIComponent(circuit.track_id)}`, { method: "DELETE", credentials: "same-origin" });
  loadCircuitModel();
}

if (HAS_DOM) {
  window.addEventListener("pitwall:pagechange", (event) => {
    if (event.detail?.page === "connection") loadCircuitModel();
  });
}

export { loadCircuitModel, formatValue };
