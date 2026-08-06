// Gate the installer download on a real activation code.
//
// The check is server-side; this file only collects the code and reports the
// answer. Nothing here decides whether a code is valid, so reading the source
// tells an attacker nothing they could not learn by typing a wrong code.

// The deployed activation Worker. It answers the CORS preflight this fetch
// triggers (POST + Content-Type: application/json is not a simple request), so
// the response is actually readable from the site's origin.
const ACTIVATION_API = "https://pitwall-activation.sarthakvij123450.workers.dev";

const form = document.getElementById("downloadForm");
const field = document.getElementById("downloadCode");
const status = document.getElementById("downloadStatus");

function say(message, tone) {
  status.textContent = message;
  status.dataset.tone = tone || "info";
}

// Format as the buyer types: upper-case, and regroup into PITW-XXXXX-XXXXX-XXXXX
// so a code pasted without hyphens, or typed in lower case, still looks right.
function tidy(raw) {
  let text = raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
  text = text.replace(/^PITW/, "");
  const groups = [text.slice(0, 5), text.slice(5, 10), text.slice(10, 15)].filter(Boolean);
  return groups.length ? "PITW-" + groups.join("-") : text ? "PITW-" + text : "";
}

if (field) {
  field.addEventListener("input", () => {
    const caretAtEnd = field.selectionStart === field.value.length;
    const tidied = tidy(field.value);
    if (tidied !== field.value) {
      field.value = tidied;
      if (caretAtEnd) field.setSelectionRange(tidied.length, tidied.length);
    }
  });
}

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = tidy(field.value);
    if (code.replace(/[^A-Z0-9]/g, "").length !== 19) {
      say("That code looks incomplete. It has 15 characters after PITW.", "error");
      field.focus();
      return;
    }

    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    say("Checking your code…");

    try {
      const response = await fetch(`${ACTIVATION_API}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        say(
          (payload && payload.message) ||
            "Could not check that code just now. Try again in a moment.",
          "error"
        );
        return;
      }

      say("Code accepted — your download is starting.", "success");
      // Navigate rather than fetch: the file is large, and letting the browser
      // handle it gives the buyer a normal download with a progress bar.
      window.location.href = payload.url;
    } catch {
      say(
        "Could not reach the server. Check your connection and try again — " +
          "if it keeps failing, email vale.scott00@gmail.com and I will send " +
          "the installer directly.",
        "error"
      );
    } finally {
      button.disabled = false;
    }
  });
}
