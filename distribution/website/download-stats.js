(() => {
  "use strict";
  const ENDPOINT = "https://pitwall-activation.sarthakvij123450.workers.dev/download-stats";
  const byId = id => document.getElementById(id);
  const form = byId("reportLogin"), field = byId("reportKey"), status = byId("reportStatus");
  const report = byId("reportData"), refresh = byId("reportRefresh");
  let accessKey = "", pending = null, generation = 0;
  const number = value => new Intl.NumberFormat().format(value);

  function clearReport() {
    report.hidden = true;
    byId("reportRows").replaceChildren();
    for (const id of ["totalAll", "totalWindows", "totalAndroid"]) byId(id).textContent = "—";
    byId("reportUpdated").textContent = "";
    byId("reportSince").textContent = "";
  }

  function lock() {
    generation++;
    if (pending) pending.abort();
    pending = null;
    refresh.disabled = false;
    accessKey = "";
    field.value = "";
    clearReport();
    form.hidden = false;
    status.textContent = "Report locked. Enter your access key to load the counts.";
    field.focus();
  }

  function validCount(value) { return Number.isSafeInteger(value) && value >= 0; }
  function validate(data) {
    if (data?.metric !== "download_starts" || !data.totals || !Array.isArray(data.daily) ||
        ![data.totals.all, data.totals.windows, data.totals.android].every(validCount) ||
        data.totals.all !== data.totals.windows + data.totals.android ||
        !Number.isFinite(Date.parse(data.generated_at)) ||
        (data.first_recorded_at !== null && !Number.isFinite(Date.parse(data.first_recorded_at))) ||
        data.daily.length > 60) throw new Error("Report data could not be read. Try again.");
    for (const row of data.daily) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row.day) || !validCount(row.starts) ||
          !["windows", "android"].includes(row.platform)) throw new Error("Report data could not be read. Try again.");
    }
  }

  function render(data) {
    byId("totalAll").textContent = number(data.totals.all);
    byId("totalWindows").textContent = number(data.totals.windows);
    byId("totalAndroid").textContent = number(data.totals.android);
    byId("reportUpdated").textContent = `Updated ${new Date(data.generated_at).toLocaleString()}`;
    byId("reportSince").textContent = data.first_recorded_at
      ? `First recorded start: ${new Date(data.first_recorded_at).toLocaleString()}. Earlier downloads are not included.`
      : "No download starts recorded yet. Earlier downloads are not included.";
    const days = new Map();
    for (const row of data.daily) {
      if (!days.has(row.day)) days.set(row.day, {windows: 0, android: 0});
      days.get(row.day)[row.platform] += row.starts;
    }
    const rows = byId("reportRows");
    rows.replaceChildren();
    const today = Date.parse(data.generated_at.slice(0, 10) + "T00:00:00Z");
    for (let i = 0; i < 30; i++) {
      const date = new Date(today - i * 86400000).toISOString().slice(0, 10);
      const counts = days.get(date) || {windows: 0, android: 0};
      const row = document.createElement("tr");
      const beforeFirst = !data.first_recorded_at || date < data.first_recorded_at.slice(0, 10);
      const cells = [date, counts.windows, counts.android, counts.windows + counts.android];
      cells.forEach((value, column) => {
        const cell = document.createElement(column === 0 ? "th" : "td");
        if (column === 0) cell.scope = "row";
        cell.textContent = column === 0 ? value : beforeFirst ? "—" : number(value);
        row.appendChild(cell);
      });
      rows.appendChild(row);
    }
    report.hidden = false;
    form.hidden = true;
  }

  async function loadReport() {
    if (pending) pending.abort();
    const current = ++generation;
    const controller = new AbortController();
    pending = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    refresh.disabled = true;
    status.textContent = "Loading download counts…";
    try {
      const response = await fetch(ENDPOINT, {
        headers: {Authorization: `Bearer ${accessKey}`},
        cache: "no-store", credentials: "omit", signal: controller.signal,
      });
      if (response.status === 401) throw new Error("Access key not accepted. Please check the dedicated report key.");
      if (!response.ok) throw new Error("Counts are temporarily unavailable. Please try again.");
      const data = await response.json();
      if (current !== generation) return;
      validate(data);
      render(data);
      status.textContent = "Counts loaded. A download start does not confirm installation.";
    } catch (error) {
      if (current !== generation) return;
      clearReport();
      form.hidden = false;
      accessKey = "";
      status.textContent = error.name === "AbortError"
        ? "The report timed out. Enter the access key and try again."
        : error instanceof TypeError ? "Could not reach the report. Check your connection and try again." : error.message;
    } finally {
      clearTimeout(timeout);
      if (current === generation) { pending = null; refresh.disabled = false; }
    }
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    accessKey = field.value.trim();
    field.value = "";
    if (!accessKey) { status.textContent = "Enter your download-report access key."; return; }
    loadReport();
  });
  refresh.addEventListener("click", loadReport);
  byId("reportLock").addEventListener("click", lock);
  window.addEventListener("pagehide", lock);
})();
