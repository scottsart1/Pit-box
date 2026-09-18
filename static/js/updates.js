/* Updates are a public version check, separate from optional usage reporting. */
(() => {
  "use strict";
  const get = id => document.getElementById(id);
  const card = get("updateSettings"), status = get("updateStatus"), toggle = get("updateToggle");
  get("settingsGroups").before(card);
  const banner = document.createElement("a");
  banner.className = "button ghost"; banner.hidden = true; banner.target = "_blank"; banner.rel = "noopener noreferrer";
  document.querySelector("header.top").appendChild(banner);
  let busy = false;
  function render(data) {
    toggle.checked = data.enabled === true;
    const release = data.release;
    const safe = release && ["https://yourpitbox.com/#windows-release", "https://yourpitbox.com/#android-release"].includes(release.download_url);
    banner.hidden = !(safe && data.available && !data.dismissed);
    if (safe) { banner.href = release.download_url; banner.textContent = `Update ${release.version} available`; }
    else { banner.removeAttribute("href"); banner.textContent = ""; }
    get("updateDownload").hidden = !(safe && data.available);
    if (safe) get("updateDownload").href = release.download_url;
    else get("updateDownload").removeAttribute("href");
    get("updateDismiss").hidden = !(safe && data.available && !data.dismissed);
    get("updateNotes").textContent = safe && data.available ? release.notes : "";
    status.textContent = data.outcome === "unavailable" ? "Update service unavailable. Your app still works normally; try later."
      : data.available && safe ? `Version ${release.version} is available. You have ${data.current_version}. Install when you are not racing.`
      : data.outcome === "ok" ? `You have ${data.current_version}. No newer release is published for this platform.`
      : data.outcome === "unsupported" ? "Automatic updates are checked for packaged Windows and Android apps."
      : data.outcome === "not_published" ? "No release announcement is published for this platform yet."
      : "No update check yet. Your app starts without waiting for the update service.";
  }
  async function request(path = "", body) {
    if (busy) return;
    busy = true; toggle.disabled = get("updateCheck").disabled = true;
    try {
      const response = await fetch("/api/v1/updates" + path, {cache: "no-store", ...(body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})});
      if (!response.ok) throw new Error("Update controls unavailable. Please try again.");
      render(await response.json());
    } catch { status.textContent = "Update controls unavailable. Your app still works normally."; }
    finally { busy = false; toggle.disabled = get("updateCheck").disabled = false; }
  }
  toggle.addEventListener("change", () => request("", {enabled: toggle.checked}));
  get("updateCheck").addEventListener("click", () => request("/check", {}));
  get("updateDismiss").addEventListener("click", () => request("", {dismiss: true}));
  setInterval(() => { if (document.visibilityState === "visible") request(); }, 60000);
  request();
})();
