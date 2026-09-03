// Reviews: read from the Worker and posted to it.
//
// Nothing a visitor submits is shown until the owner has read it, so a fresh
// submission is acknowledged as received, not published. Review text is
// rendered with textContent only; nothing from the server or the visitor is
// ever treated as markup.

// Wrapped in a function scope: download.js is a classic script on the same
// page, and top-level const declarations in classic scripts share one global
// scope, so a second `status` or `form` would be a SyntaxError that stops
// this whole file from running.
(() => {
  const ACTIVATION_API = "https://pitwall-activation.sarthakvij123450.workers.dev";
  const REVIEWS_URL = `${ACTIVATION_API}/reviews`;

  const list = document.getElementById("reviewList");
  const empty = document.getElementById("reviewsEmpty");
  const summary = document.getElementById("reviewSummary");
  const form = document.getElementById("reviewForm");
  const status = document.getElementById("reviewStatus");

  function say(message, tone) {
    if (!status) return;
    status.textContent = message;
    status.dataset.tone = tone || "info";
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function stars(rating) {
    return "★".repeat(rating) + "☆".repeat(5 - rating);
  }

  function monthOf(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(undefined, { year: "numeric", month: "long" });
  }

  function render(payload) {
    if (!list) return;
    const reviews = Array.isArray(payload.reviews) ? payload.reviews : [];
    list.replaceChildren();
    if (!reviews.length) {
      if (empty) empty.hidden = false;
      if (summary) summary.textContent = "";
      return;
    }
    if (empty) empty.hidden = true;
    if (summary) {
      const noun = payload.count === 1 ? "review" : "reviews";
      summary.textContent = `${payload.average} out of 5, from ${payload.count} ${noun}.`;
    }
    for (const review of reviews) {
      const item = node("article", "review");
      const head = node("div", "review-head");
      const rating = node("span", "review-stars", stars(Number(review.rating) || 0));
      rating.setAttribute("aria-label", `${review.rating} out of 5`);
      const when = node("time", "review-when", monthOf(review.created_at));
      when.dateTime = String(review.created_at || "");
      head.append(rating, node("span", "review-name", String(review.name || "")), when);
      item.append(head, node("p", "review-body", String(review.body || "")));
      list.append(item);
    }
  }

  async function load() {
    try {
      const response = await fetch(REVIEWS_URL);
      if (!response.ok) throw new Error(String(response.status));
      render(await response.json());
    } catch {
      // The empty-state text is in the page already; leave it.
    }
  }

  async function submit(review) {
    const response = await fetch(REVIEWS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(review),
    });
    const payload = await response.json().catch(() => null);
    return { ok: response.ok, message: payload && payload.message };
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const review = {
        name: String(data.get("name") || "").trim(),
        rating: Number(data.get("rating") || 0),
        body: String(data.get("body") || "").trim(),
        email: String(data.get("email") || "").trim(),
        website: String(data.get("website") || ""),
      };

      if (!review.name) {
        say("Add the name you want shown with the review.", "error");
        return;
      }
      if (!review.rating) {
        say("Pick a star rating.", "error");
        return;
      }
      if (review.body.length < 20) {
        say("Say a little more: at least 20 characters.", "error");
        return;
      }

      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      say("Sending…");
      try {
        const result = await submit(review);
        if (result.ok) {
          form.reset();
          say("Thank you. I read every review before it goes up, so yours will appear once I have seen it.", "success");
        } else {
          say(result.message || "That did not go through. Try again in a moment.", "error");
        }
      } catch {
        say("Could not reach the server. Try again in a moment, or email vale.scott00@gmail.com.", "error");
      } finally {
        button.disabled = false;
      }
    });
  }

  load();
})();
