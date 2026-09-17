// The free download, and the optional release-news signup in front of it.
//
// Nothing here gates anything. The installer URL is public, and the email is a
// courtesy the visitor can decline with one click. Skip downloads the selected
// platform; Cancel/Escape closes without downloading. The address goes to the Worker's
// /subscribe route, which stores it for release announcements and nothing else.

// The deployed activation Worker. It streams the installer at /installer and
// records signups at /subscribe. It answers the CORS preflight the JSON POST
// triggers, so the response is actually readable from the site's origin.
const ACTIVATION_API = "https://pitwall-activation.sarthakvij123450.workers.dev";
const INSTALLER_URL = `${ACTIVATION_API}/installer`;
const ANDROID_URL = `${ACTIVATION_API}/android`;
const INSTALLER_INFO_URL = `${ACTIVATION_API}/installer-info`;
const INSTALLER_INFO_TIMEOUT_MS = 5000;

const button = document.getElementById("downloadButton");
const androidButtons = document.querySelectorAll?.('[data-download-platform="android"]') || [];
const status = document.getElementById("downloadStatus");
const androidStatus = document.getElementById("androidDownloadStatus");
const modal = document.getElementById("emailModal");
const form = document.getElementById("emailForm");
const field = document.getElementById("emailField");
const emailStatus = document.getElementById("emailStatus");
const skip = document.getElementById("skipEmail");
const codePanel = document.getElementById("codePanel");
const codeOutput = document.getElementById("freeCode");
const copyCode = document.getElementById("copyCode");
const platformLabel = document.getElementById("emailModalPlatform");
let selectedPlatform = "windows", modalGeneration = 0, pendingSignup = null;

function say(element, message, tone) {
  if (!element) return;
  element.textContent = message;
  element.dataset.tone = tone || "info";
}

// While the installer in R2 is a build from before the free edition, it still
// asks for an activation code on its first start. The Worker says so, and
// hands over the one shared code; the page shows it beside the download so
// nobody is stranded at that window. Once a free-edition installer is
// uploaded the Worker answers needs_code:false and the panel never appears.
async function installerInfo() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), INSTALLER_INFO_TIMEOUT_MS);
  try {
    const response = await fetch(INSTALLER_INFO_URL, { signal: controller.signal });
    if (!response.ok) return { needs_code: false, code: null, unknown: true };
    const info = await response.json();
    if (info && info.needs_code === false) return { needs_code: false, code: null };
    if (info && info.needs_code === true && typeof info.code === "string" && info.code.trim()) {
      return { needs_code: true, code: info.code.trim() };
    }
    // Metadata is advisory: a malformed reply must not stop the download or
    // display a value that is not actually an activation code.
    return { needs_code: false, code: null, unknown: true };
  } catch {
    return { needs_code: false, code: null, unknown: true };
  } finally {
    clearTimeout(timeout);
  }
}

function showCode(code) {
  if (!codePanel || !codeOutput) return;
  codeOutput.textContent = code;
  codePanel.hidden = false;
}

// Navigate rather than fetch: the file is large, and letting the browser
// handle it gives a normal download with a progress bar and resume. A
// download navigation leaves the page in place, so the code panel stays
// readable while the file arrives.
async function startDownload(message, tone, platform = "windows") {
  // A repeat download may see a newly published installer. Do not retain a
  // bridge code from an earlier response when this download needs none.
  if (codePanel) codePanel.hidden = true;
  if (codeOutput) codeOutput.textContent = "";
  const info = platform === "windows" ? await installerInfo() : {needs_code: false};
  if (info.needs_code && info.code) {
    showCode(info.code);
    message += " The installer asks for an activation code the first time it starts: use the one shown below.";
  } else if (info.unknown) {
    message += " Installer details could not be checked. If it asks for an activation code, follow the setup guide's upgrade steps or contact support.";
  }
  message += platform === "android"
    ? " Open the downloaded APK on your Android device when it finishes."
    : " Run PitWall-Setup.exe when it finishes.";
  say(platform === "android" ? androidStatus || status : status, message, tone);
  window.location.href = platform === "android" ? ANDROID_URL : INSTALLER_URL;
}

let lastFocus = null;

function onKey(event) {
  if (event.key === "Escape") { event.preventDefault(); closeModal(); }
  if (event.key === "Tab") {
    const focusable = Array.from(modal.querySelectorAll('input, button, a[href]')).filter(element => !element.disabled && !element.hidden);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
}

function openModal(platform = "windows") {
  modalGeneration++;
  if (pendingSignup) pendingSignup.abort();
  pendingSignup = null;
  form.querySelector("button[type=submit]").disabled = false;
  selectedPlatform = platform;
  if (platformLabel) platformLabel.textContent = platform === "android" ? "Android APK download" : "Windows installer download";
  lastFocus = document.activeElement;
  modal.hidden = false;
  say(emailStatus, "");
  field.focus();
  document.addEventListener("keydown", onKey);
}

function closeModal() {
  modalGeneration++;
  if (pendingSignup) pendingSignup.abort();
  pendingSignup = null;
  form.querySelector("button[type=submit]").disabled = false;
  modal.hidden = true;
  document.removeEventListener("keydown", onKey);
  if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
}

// Loose on purpose: the Worker validates properly, and a false rejection here
// would stop someone downloading over a typo in an optional field.
function looksLikeEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value);
}

async function subscribe(email, platform, signal) {
  const response = await fetch(`${ACTIVATION_API}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, source: platform === "android" ? "website-download-android" : "website-download" }),
    signal,
  });
  const payload = await response.json().catch(() => null);
  return { ok: response.ok, message: payload && payload.message };
}

if ((button || androidButtons.length) && modal && form && field && skip) {
  if (button) button.addEventListener("click", () => openModal("windows"));
  androidButtons.forEach(link => link.addEventListener("click", event => {
    // Keep native link behavior for new-tab gestures and a real href if JS fails.
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
    event.preventDefault();
    openModal("android");
  }));

  skip.addEventListener("click", () => {
    const platform = selectedPlatform;
    closeModal();
    return startDownload("Your download is starting.", "success", platform);
  });

  modal.querySelectorAll("[data-close]").forEach((element) => {
    element.addEventListener("click", closeModal);
  });

  if (copyCode && codeOutput) {
    copyCode.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(codeOutput.textContent.trim());
        copyCode.textContent = "Copied";
        setTimeout(() => { copyCode.textContent = "Copy code"; }, 2000);
      } catch {
        // No clipboard permission: the code is still selectable text.
      }
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pendingSignup) return;
    const platform = selectedPlatform;
    const email = field.value.trim();

    if (!email) {
      closeModal();
      await startDownload("Your download is starting.", "success", platform);
      return;
    }
    if (!looksLikeEmail(email)) {
      say(emailStatus, "That does not look like an email address. Fix it, or skip the email.", "error");
      field.focus();
      return;
    }

    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    say(emailStatus, "Saving…");
    const generation = modalGeneration;
    const controller = new AbortController();
    pendingSignup = controller;
    const timeout = setTimeout(() => controller.abort(), 8000);

    let message = "Thanks — you are on the list. Your download is starting.";
    let tone = "success";
    try {
      const result = await subscribe(email, platform, controller.signal);
      if (!result.ok) {
        message = (result.message || "The signup did not go through") +
          " Your download is starting anyway.";
        tone = "info";
      }
    } catch {
      message = "Could not reach the signup server, but your download is starting anyway.";
      tone = "info";
    } finally {
      clearTimeout(timeout);
      // A cancelled prompt, Skip, or another platform selection owns the UI now.
      if (generation !== modalGeneration) return;
      submit.disabled = false;
      closeModal();
      await startDownload(message, tone, platform);
    }
  });
}
