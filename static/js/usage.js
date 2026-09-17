/* Optional usage choice belongs to the backend installation, not this browser. */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const prompt = byId("usagePrompt"), toggle = byId("usageToggle"), status = byId("usageStatus");
  const card = byId("usageSettings");
  byId("settingsGroups").before(card);
  let enabled = false, busy = false;
  let lastInteraction = Date.now();
  const signal = () => { lastInteraction = Date.now(); };
  for (const event of ["pointerdown", "keydown", "touchstart"]) window.addEventListener(event, signal, {passive: true});

  function render(data) {
    enabled = data.enabled === true;
    toggle.checked = enabled;
    prompt.hidden = data.decided === true;
    status.textContent = enabled
      ? `Sharing is on. ${data.last_sent_at ? "Last report: " + new Date(data.last_sent_at).toLocaleString() + "." : "Reports wait safely when offline."}`
      : "Sharing is off. No usage reports are sent.";
  }
  async function refresh() {
    try {
      const response = await fetch("/api/v1/usage", {cache: "no-store"});
      if (!response.ok) throw new Error("unavailable");
      render(await response.json());
      toggle.disabled = false;
    } catch {
      status.textContent = "Usage settings unavailable. Nothing has been enabled here.";
      toggle.disabled = true;
    }
  }
  async function choose(value) {
    if (busy) return;
    busy = true;
    const controls = [toggle, byId("usageYes"), byId("usageNo")];
    controls.forEach(control => { control.disabled = true; });
    status.textContent = "Saving your choice…";
    byId("usagePromptStatus").textContent = "Saving…";
    try {
      const response = await fetch("/api/v1/usage", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: value})});
      if (!response.ok) throw new Error("not saved");
      render(await response.json());
      byId("usagePromptStatus").textContent = "";
      if (enabled) active();
    } catch {
      toggle.checked = enabled;
      status.textContent = "Choice was not saved. Please try again.";
      byId("usagePromptStatus").textContent = "Choice was not saved. Please try again.";
    } finally {
      busy = false;
      controls.forEach(control => { control.disabled = false; });
    }
  }
  async function active() {
    // A background tab or an unattended open dashboard does not count as use.
    if (!enabled || document.visibilityState !== "visible" || !document.hasFocus() || Date.now() - lastInteraction > 120000) return;
    try { await fetch("/api/v1/usage/active", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}); } catch { /* optional */ }
  }
  toggle.addEventListener("change", () => choose(toggle.checked));
  byId("usageYes").addEventListener("click", () => choose(true));
  byId("usageNo").addEventListener("click", () => choose(false));
  window.addEventListener("pitwall:pagechange", event => { if (event.detail?.page === "settings") refresh(); });
  setInterval(active, 60000);
  refresh();
})();
