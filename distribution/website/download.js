// The free download, and the optional release-news signup in front of it.
//
// Nothing here gates anything. The installer URL is public, and the email is a
// courtesy the visitor can decline with one click: every path out of the
// prompt ends in the same download. The address goes only to the Worker's
// /subscribe route, which stores it for release announcements and nothing else.

// The deployed activation Worker. It streams the installer at /installer and
// records signups at /subscribe. It answers the CORS preflight the JSON POST
// triggers, so the response is actually readable from the site's origin.
const ACTIVATION_API = "https://pitwall-activation.sarthakvij123450.workers.dev";
const INSTALLER_URL = `${ACTIVATION_API}/installer`;

const button = document.getElementById("downloadButton");
const status = document.getElementById("downloadStatus");
const modal = document.getElementById("emailModal");
const form = document.getElementById("emailForm");
const field = document.getElementById("emailField");
const emailStatus = document.getElementById("emailStatus");
const skip = document.getElementById("skipEmail");

function say(element, message, tone) {
  if (!element) return;
  element.textContent = message;
  element.dataset.tone = tone || "info";
}

// Navigate rather than fetch: the file is large, and letting the browser
// handle it gives a normal download with a progress bar and resume.
function startDownload(message, tone) {
  say(status, message, tone);
  window.location.href = INSTALLER_URL;
}

let lastFocus = null;

function onKey(event) {
  if (event.key === "Escape") closeModal();
}

function openModal() {
  lastFocus = document.activeElement;
  modal.hidden = false;
  say(emailStatus, "");
  field.focus();
  document.addEventListener("keydown", onKey);
}

function closeModal() {
  modal.hidden = true;
  document.removeEventListener("keydown", onKey);
  if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
}

// Loose on purpose: the Worker validates properly, and a false rejection here
// would stop someone downloading over a typo in an optional field.
function looksLikeEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value);
}

async function subscribe(email) {
  const response = await fetch(`${ACTIVATION_API}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, source: "website-download" }),
  });
  const payload = await response.json().catch(() => null);
  return { ok: response.ok, message: payload && payload.message };
}

if (button && modal && form && field && skip) {
  button.addEventListener("click", openModal);

  skip.addEventListener("click", () => {
    closeModal();
    startDownload("Your download is starting. Run PitWall-Setup.exe when it finishes.", "success");
  });

  modal.querySelectorAll("[data-close]").forEach((element) => {
    element.addEventListener("click", closeModal);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = field.value.trim();

    if (!email) {
      closeModal();
      startDownload("Your download is starting. Run PitWall-Setup.exe when it finishes.", "success");
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

    let message = "Thanks — you are on the list. Your download is starting.";
    let tone = "success";
    try {
      const result = await subscribe(email);
      if (!result.ok) {
        message = (result.message || "The signup did not go through") +
          " Your download is starting anyway.";
        tone = "info";
      }
    } catch {
      message = "Could not reach the signup server, but your download is starting anyway.";
      tone = "info";
    } finally {
      submit.disabled = false;
      closeModal();
      startDownload(message, tone);
    }
  });
}
