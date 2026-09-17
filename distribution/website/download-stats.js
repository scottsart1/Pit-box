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
    byId("historyData").hidden = true;
    byId("historyRows").replaceChildren();
    for (const id of ["historyAll", "historyWindows", "historyAndroid"]) byId(id).textContent = "—";
    byId("historyPeriod").textContent = "";
    byId("historyStatus").textContent = "";
    for (const id of ["combinedAll", "combinedWindows", "combinedAndroid"]) byId(id).textContent = "—";
    byId("combinedSources").textContent = "";
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
    if (data.history != null) {
      validateHistory(data.history);
      if (["windows", "android", "all"].some(key => !validCount(data.totals[key] + data.history.totals[key]))) {
        throw new Error("Combined data could not be read. Try again.");
      }
    }
  }

  function validateHistory(history) {
    const bad = () => { throw new Error("Historical data could not be read. Try again."); };
    if (history.metric !== "historical_file_requests" || history.source !== "Cloudflare R2 analytics" ||
        ![history.from, history.until, history.recovered_at].every(value => typeof value === "string" && Number.isFinite(Date.parse(value))) ||
        Date.parse(history.from) >= Date.parse(history.until) ||
        Date.parse(history.until) - Date.parse(history.from) > 366 * 86400000 ||
        !history.totals || ![history.totals.windows, history.totals.android, history.totals.all].every(validCount) ||
        !Array.isArray(history.daily) || history.daily.length > 730) bad();
    const totals = {windows: 0, android: 0, all: 0}, seen = new Set();
    for (const row of history.daily) {
      const key = `${row.day}/${row.platform}`;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row.day) || row.day < history.from.slice(0, 10) ||
          row.day > history.until.slice(0, 10) || !["windows", "android"].includes(row.platform) ||
          !validCount(row.requests) || seen.has(key)) bad();
      seen.add(key);
      totals[row.platform] += row.requests;
      totals.all += row.requests;
    }
    if (Object.keys(totals).some(key => !validCount(totals[key]) || totals[key] !== history.totals[key])) bad();
  }

  function renderHistory(history) {
    byId("historyData").hidden = true;
    byId("historyRows").replaceChildren();
    byId("historyStatus").textContent = history
      ? "Source: Cloudflare R2 analytics. Historical requests are not added to the live download-start totals."
      : "Historical records have not been imported. This is unavailable history, not a zero count.";
    if (!history) return;
    byId("historyAll").textContent = number(history.totals.all);
    byId("historyWindows").textContent = number(history.totals.windows);
    byId("historyAndroid").textContent = number(history.totals.android);
    const utc = value => new Date(value).toLocaleString(undefined, {timeZone: "UTC"});
    byId("historyPeriod").textContent = `Coverage: ${utc(history.from)} UTC to ${utc(history.until)} UTC (end exclusive). Recovered ${utc(history.recovered_at)} UTC. Outside this window, history is not included.`;
    const days = new Map();
    for (const row of history.daily) {
      if (!days.has(row.day)) days.set(row.day, {windows: 0, android: 0});
      days.get(row.day)[row.platform] += row.requests;
    }
    const firstDay = Date.parse(history.from.slice(0, 10) + "T00:00:00Z");
    const lastDay = Date.parse(new Date(Date.parse(history.until) - 1).toISOString().slice(0, 10) + "T00:00:00Z");
    for (let stamp = lastDay; stamp >= firstDay; stamp -= 86400000) {
      const day = new Date(stamp).toISOString().slice(0, 10);
      const counts = days.get(day) || {windows: 0, android: 0};
      const tr = document.createElement("tr");
      [day, counts.windows, counts.android, counts.windows + counts.android].forEach((value, i) => {
        const td = document.createElement(i === 0 ? "th" : "td");
        if (i === 0) td.scope = "row";
        td.textContent = i === 0 ? value : number(value);
        tr.appendChild(td);
      });
      byId("historyRows").appendChild(tr);
    }
    byId("historyData").hidden = false;
  }

  function render(data) {
    for (const [platform, id] of [["all", "combinedAll"], ["windows", "combinedWindows"], ["android", "combinedAndroid"]]) {
      byId(id).textContent = data.history ? number(data.totals[platform] + data.history.totals[platform]) : "—";
    }
    byId("combinedSources").textContent = data.history
      ? `${number(data.history.totals.all)} historical file requests + ${number(data.totals.all)} live download starts. Different counting methods; see coverage and limitations below.`
      : "Historical records are unavailable, so a combined total cannot be shown. The live counts below are still available.";
    byId("totalAll").textContent = number(data.totals.all);
    byId("totalWindows").textContent = number(data.totals.windows);
    byId("totalAndroid").textContent = number(data.totals.android);
    byId("reportUpdated").textContent = `Updated ${new Date(data.generated_at).toLocaleString()}`;
    byId("reportSince").textContent = data.first_recorded_at
      ? `First recorded live start: ${new Date(data.first_recorded_at).toLocaleString()}. Historical requests below are not included in these totals.`
      : "No live download starts recorded yet. Historical requests below are kept separate.";
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
    renderHistory(data.history);
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
      status.textContent = data.history ? "History and live counts loaded. The overview combines activity measured in two different ways." : "Live counts loaded; historical activity is unavailable.";
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
