(() => {
  "use strict";
  const BASE = "https://pitwall-activation.sarthakvij123450.workers.dev";
  const el = id => document.getElementById(id);
  const number = value => new Intl.NumberFormat().format(value);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const platforms = ["windows", "android"];
  const titles = {app_started: "Opened the app", app_used: "Actively used the app", racing: "Qualifying driving telemetry", engineer: "Engineer replies", voice: "Voice commands", analysis: "Lap comparisons", transfer: "History-transfer starts"};
  const tableIds = ["downloadPlatformRows", "downloadDailyRows", "emailRows", "signupSourceRows", "usageSignalRows", "retentionRows", "versionRows"];
  const metricIds = ["metricDownloads", "metricRecentDownloads", "metricEmails", "metricActive", "metricRacing", "metricRetention"];
  const requests = new Set();
  let key = "", generation = 0, busy = false, copying = false;
  let lastInteraction = Date.now(), hiddenAt = null, emailSnapshot = null, emailNext = null, emailCount = 0;
  const text = (id, value) => { el(id).textContent = value; };
  const interact = () => { lastInteraction = Date.now(); };
  for (const event of ["pointerdown", "keydown", "touchstart"]) window.addEventListener(event, interact, {passive: true});

  function clearData() {
    for (const id of tableIds) el(id).replaceChildren();
    for (const id of metricIds) text(id, "—");
    for (const id of ["ownerUpdated", "metricPlatformSplit", "metricNewEmails", "historyOverview", "historyCoverage", "downloadAvailability", "usageAvailability", "emailStatus"]) text(id, "");
    el("emailCopyText").value = "";
    el("emailCopyFallback").hidden = true;
    el("loadMoreEmails").hidden = true;
    el("loadMoreEmails").disabled = false;
    el("copyAllEmails").disabled = true;
    emailSnapshot = emailNext = null;
    emailCount = 0;
  }
  function lock(message = "Locked. No private data is loaded.") {
    generation++;
    for (const request of requests) request.abort();
    requests.clear();
    key = el("ownerKey").value = "";
    busy = copying = false;
    clearData();
    el("ownerData").hidden = el("ownerLock").hidden = true;
    el("ownerLoginPanel").hidden = false;
    el("ownerOpen").disabled = el("ownerRefresh").disabled = false;
    text("ownerStatus", message);
  }
  function expired() {
    return Date.now() - lastInteraction >= 15 * 60000 || (hiddenAt !== null && Date.now() - hiddenAt >= 5 * 60000);
  }
  async function api(path, current) {
    if (!key || current !== generation) throw new Error("Locked");
    if (expired()) { lock("Session locked. Enter your key again."); throw new Error("Locked"); }
    const controller = new AbortController();
    requests.add(controller);
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(BASE + path, {headers: {Authorization: `Bearer ${key}`}, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", redirect: "error", signal: controller.signal});
      if (current !== generation) throw new Error("Locked");
      if (response.status === 401) { lock("Access key not accepted. Use your private owner dashboard key."); throw new Error("Unauthorized"); }
      if (!response.ok) throw new Error(response.status === 429 ? "Request limit reached. Please wait one minute." : "This data is temporarily unavailable. Please retry.");
      const result = await response.json();
      if (current !== generation) throw new Error("Locked");
      return result;
    } finally { clearTimeout(timer); requests.delete(controller); }
  }
  function row(id, values) {
    const tr = document.createElement("tr");
    for (const value of values) { const td = document.createElement("td"); td.textContent = String(value); tr.appendChild(td); }
    el(id).appendChild(tr);
    return tr;
  }
  function array(value, max = 100) { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid report data."); return value; }
  function counted(value) { if (!count(value)) throw new Error("Invalid report count."); return value; }
  function validateOverview(data) {
    if (!data || !Number.isFinite(Date.parse(data.generated_at))) throw new Error("Invalid report data.");
    if (data.downloads !== null) {
      const d = data.downloads;
      if (d?.metric !== "download_starts" || !d.totals) throw new Error("Invalid download data.");
      for (const p of [...platforms, "all"]) counted(d.totals[p]);
      if (d.totals.all !== d.totals.windows + d.totals.android) throw new Error("Download totals do not reconcile.");
      for (const r of array(d.daily, 60)) if (!platforms.includes(r.platform) || !/^\d{4}-\d{2}-\d{2}$/.test(r.day) || !count(r.starts)) throw new Error("Invalid daily downloads.");
      if (d.history) {
        if (d.history.metric !== "historical_file_requests") throw new Error("Invalid historical data.");
        for (const p of [...platforms, "all"]) counted(d.history.totals?.[p]);
      }
    }
    if (data.subscribers !== null) {
      const s = data.subscribers;
      if (s?.metric !== "newsletter_subscribers") throw new Error("Invalid subscriber data.");
      counted(s.total); counted(s.new_30d);
      for (const r of array(s.sources, 50)) if (typeof r.source !== "string" || !count(r.total)) throw new Error("Invalid signup sources.");
    }
    if (data.usage !== null) {
      const u = data.usage;
      if (u?.metric !== "opted_in_installations") throw new Error("Invalid usage data.");
      for (const r of array(u.installations, 2)) if (!platforms.includes(r.platform) || !count(r.installations) || !count(r.new_30d)) throw new Error("Invalid installation totals.");
      for (const r of array(u.features, 14)) if (!platforms.includes(r.platform) || !Object.hasOwn(titles, r.event) || !count(r.installations)) throw new Error("Invalid feature counts.");
      for (const r of array(u.recent, 2)) if (!platforms.includes(r.platform) || !count(r.active_7d) || !count(r.active_today)) throw new Error("Invalid active counts.");
      if (array(u.retention, 3).length !== 3) throw new Error("Invalid retention.");
      u.retention.forEach((c, i) => {
        if (c.day !== [1, 7, 30][i]) throw new Error("Invalid cohort.");
        for (const r of array(c.platforms, 2)) if (!platforms.includes(r.platform) || !count(r.eligible) || !count(r.returned) || r.returned > r.eligible) throw new Error("Invalid retention counts.");
      });
      for (const r of array(u.versions, 50)) if (!platforms.includes(r.platform) || typeof r.version !== "string" || r.version.length > 40 || !count(r.installations)) throw new Error("Invalid versions.");
    }
  }
  const sum = (rows, field) => rows.reduce((total, r) => total + r[field], 0);
  const returnRate = rows => {
    const eligible = sum(rows, "eligible"), returned = sum(rows, "returned");
    return eligible ? `${number(returned)} / ${number(eligible)} (${(100 * returned / eligible).toFixed(1)}%)` : "Not yet eligible";
  };
  function renderOverview(data) {
    validateOverview(data);
    for (const id of tableIds.filter(id => id !== "emailRows")) el(id).replaceChildren();
    for (const id of metricIds) text(id, "—");
    text("metricPlatformSplit", "Windows / Android"); text("metricNewEmails", "Release-news signups");
    text("ownerUpdated", `Updated ${new Date(data.generated_at).toLocaleString()} · dates below use UTC`);
    const d = data.downloads;
    text("downloadAvailability", d ? (d.first_recorded_at ? `Live tracking since ${d.first_recorded_at.slice(0, 10)}.` : "No live download starts recorded yet.") : "Download data unavailable—not zero.");
    text("historyOverview", d?.history ? `${number(d.history.totals.all)} historical file reads · Windows ${number(d.history.totals.windows)} · Android ${number(d.history.totals.android)}` : "Historical snapshot unavailable—not zero.");
    text("historyCoverage", d?.history ? `${d.history.from} to ${d.history.until} · Cloudflare R2 analytics. Not added to live starts.` : "");
    if (d) {
      text("metricDownloads", number(d.totals.all));
      text("metricRecentDownloads", number(sum(d.daily, "starts")));
      const recent = Object.fromEntries(platforms.map(p => [p, sum(d.daily.filter(r => r.platform === p), "starts")]));
      text("metricPlatformSplit", `${number(recent.windows)} Windows · ${number(recent.android)} Android`);
      for (const p of platforms) row("downloadPlatformRows", [p === "windows" ? "Windows" : "Android", number(d.totals[p]), number(recent[p])]);
      const today = new Date(data.generated_at).toISOString().slice(0, 10), daily = [];
      for (let i = 0; i < 30; i++) {
        const day = new Date(Date.parse(today) - i * 86400000).toISOString().slice(0, 10);
        const started = d.first_recorded_at && day >= d.first_recorded_at.slice(0, 10);
        const values = platforms.map(p => sum(d.daily.filter(r => r.day === day && r.platform === p), "starts"));
        daily.push({day, started, values, total: values[0] + values[1]});
      }
      const max = Math.max(1, ...daily.map(r => r.total));
      for (const r of daily) {
        const tr = row("downloadDailyRows", [r.day, ...r.values.map(v => r.started ? number(v) : "—"), r.started ? number(r.total) : "—"]);
        const td = document.createElement("td"), bar = document.createElement("progress");
        if (r.started) { bar.max = max; bar.value = r.total; bar.setAttribute("aria-label", `${r.total} starts on ${r.day}`); td.appendChild(bar); }
        tr.appendChild(td);
      }
    }
    const s = data.subscribers;
    if (s) {
      text("metricEmails", number(s.total)); text("metricNewEmails", `${number(s.new_30d)} new in the last 30 days`);
      for (const source of s.sources) row("signupSourceRows", [source.source, number(source.total)]);
    }
    const u = data.usage;
    text("usageAvailability", u ? (u.installations.length ? "Based on installations that enabled sharing. Daily feature flags, not individual actions." : "No opted-in usage reports yet. This does not mean nobody is using the apps.") : "Usage data unavailable—not zero.");
    if (u) {
      text("metricActive", number(sum(u.recent, "active_7d")));
      text("metricRacing", number(sum(u.features.filter(r => r.event === "racing"), "installations")));
      text("metricRetention", returnRate(u.retention[1].platforms));
      const addSignal = (title, values) => row("usageSignalRows", [title, ...values.map(number), number(values[0] + values[1])]);
      addSignal("First reported use", platforms.map(p => sum(u.installations.filter(r => r.platform === p), "new_30d")));
      for (const [event, title] of Object.entries(titles)) addSignal(title, platforms.map(p => sum(u.features.filter(r => r.platform === p && r.event === event), "installations")));
      for (const c of u.retention) row("retentionRows", [`Day ${c.day}`, ...platforms.map(p => returnRate(c.platforms.filter(r => r.platform === p))), returnRate(c.platforms)]);
      for (const v of u.versions) row("versionRows", [v.platform, v.version, number(v.installations)]);
    }
    text("ownerStatus", [d, s, u].every(Boolean) ? "Connected. Collecting automatically; no manual imports needed." : "Connected, but some reports are unavailable. Missing data is not shown as zero.");
  }
  function validateEmails(data) {
    if (!data || !count(data.snapshot) || !count(data.total) || (data.next !== null && (!count(data.next) || data.next <= 0 || data.next > data.snapshot))) throw new Error("Invalid email page.");
    for (const r of array(data.subscribers, 200)) if (typeof r.email !== "string" || r.email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email) || typeof r.created_at !== "string" || !Number.isFinite(Date.parse(r.created_at)) || (r.source !== null && typeof r.source !== "string")) throw new Error("Invalid email record.");
    return data;
  }
  async function loadEmails(current, append = false) {
    const query = append ? `?limit=100&snapshot=${emailSnapshot}&before=${emailNext}` : "?limit=100";
    const data = validateEmails(await api("/owner/subscribers" + query, current));
    if (current !== generation) return;
    if (!append) { el("emailRows").replaceChildren(); emailCount = 0; }
    emailSnapshot = data.snapshot; emailNext = data.next; emailCount += data.subscribers.length;
    for (const item of data.subscribers) row("emailRows", [item.email, item.created_at.replace("T", " ").slice(0, 19), item.source || "unknown"]);
    text("emailStatus", data.total ? `Showing ${number(emailCount)} of ${number(data.total)} subscribers.` : "No newsletter signups yet.");
    el("loadMoreEmails").hidden = emailNext === null;
    el("copyAllEmails").disabled = data.total === 0;
  }
  async function refresh() {
    if (!key || busy || copying) return;
    if (expired()) { lock("Session locked after inactivity. Enter your key again."); return; }
    const current = generation;
    busy = true; el("ownerRefresh").disabled = true;
    try {
      const data = await api("/owner/overview", current);
      if (current !== generation) return;
      renderOverview(data);
      el("ownerLoginPanel").hidden = true; el("ownerData").hidden = el("ownerLock").hidden = false;
      try { await loadEmails(current); }
      catch (error) {
        if (current !== generation) return;
        el("emailRows").replaceChildren(); el("copyAllEmails").disabled = true; el("loadMoreEmails").hidden = true;
        text("emailStatus", error.message);
      }
    } catch (error) {
      if (current !== generation) return;
      clearData();
      text("ownerStatus", error.name === "AbortError" ? "Refresh timed out. No stale numbers are displayed; retry shortly." : error.message);
      if (el("ownerData").hidden) key = "";
    } finally {
      if (current === generation) { busy = false; el("ownerRefresh").disabled = el("ownerOpen").disabled = false; }
    }
  }
  async function copyAll() {
    if (!key || copying || busy) return;
    const current = generation;
    copying = true; el("copyAllEmails").disabled = true;
    el("emailCopyText").value = ""; el("emailCopyFallback").hidden = true;
    const addresses = new Set();
    try {
      let path = "/owner/subscribers?limit=200", snapshot = null, expectedTotal = null, previous = Infinity;
      do {
        const data = validateEmails(await api(path, current));
        if (current !== generation) return;
        if (data.total > 50000) throw new Error("This list is too large for a safe browser copy. No partial list was copied.");
        snapshot ??= data.snapshot;
        expectedTotal ??= data.total;
        if (expectedTotal !== data.total) throw new Error("The subscriber list changed during copying. Retry; no partial list was copied.");
        if (snapshot !== data.snapshot || (data.next !== null && data.next >= previous)) throw new Error("Email pagination changed. Retry; no partial list was copied.");
        for (const item of data.subscribers) addresses.add(item.email);
        if (addresses.size > 50000) throw new Error("Copy limit reached. No partial list was copied.");
        text("emailStatus", `Preparing all emails… ${number(addresses.size)} loaded.`);
        previous = data.next;
        path = data.next === null ? null : `/owner/subscribers?limit=200&snapshot=${snapshot}&before=${data.next}`;
      } while (path);
      if (current !== generation || !key) return;
      if (addresses.size !== expectedTotal) throw new Error("The complete subscriber list could not be verified. No partial list was copied.");
      const value = [...addresses].join("\n");
      if (!value) { text("emailStatus", "No email addresses to copy."); return; }
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(value);
        if (current === generation) text("emailStatus", `Copied all ${number(addresses.size)} email addresses, one per line. No email was sent.`);
      } catch {
        if (current !== generation) return;
        el("emailCopyText").value = value; el("emailCopyFallback").hidden = false;
        text("emailStatus", `All ${number(addresses.size)} addresses are ready below. Your browser blocked automatic copying.`);
      }
    } catch (error) { if (current === generation) text("emailStatus", error.name === "AbortError" ? "Email loading timed out. Nothing was copied." : error.message); }
    finally { addresses.clear(); if (current === generation) { copying = false; el("copyAllEmails").disabled = false; } }
  }
  el("ownerLogin").addEventListener("submit", event => {
    event.preventDefault();
    const submitted = el("ownerKey").value.trim();
    lock();
    if (!submitted) return;
    key = submitted;
    interact();
    el("ownerOpen").disabled = true;
    text("ownerStatus", "Opening private dashboard…");
    refresh();
  });
  el("ownerRefresh").addEventListener("click", () => { interact(); return refresh(); });
  el("ownerLock").addEventListener("click", () => lock());
  el("copyAllEmails").addEventListener("click", copyAll);
  el("selectEmails").addEventListener("click", () => { el("emailCopyText").focus(); el("emailCopyText").select(); });
  el("loadMoreEmails").addEventListener("click", async () => {
    if (busy || copying || emailNext === null) return;
    const current = generation; busy = true; el("loadMoreEmails").disabled = true;
    try { await loadEmails(current, true); } catch (error) { if (current === generation) text("emailStatus", error.message); }
    finally { if (current === generation) { busy = false; el("loadMoreEmails").disabled = false; } }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") hiddenAt = Date.now();
    else { if (key && expired()) lock("Dashboard locked while hidden. Enter your key again."); hiddenAt = null; if (key && el("ownerAuto").checked) refresh(); }
  });
  window.addEventListener("pagehide", () => lock());
  setInterval(() => {
    if (!key) return;
    if (expired()) { lock("Dashboard locked after inactivity. Enter your key again."); return; }
    if (document.visibilityState === "visible" && el("ownerAuto").checked) refresh();
  }, 60000);
})();
