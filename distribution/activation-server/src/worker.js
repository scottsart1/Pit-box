// Pit Wall download + activation endpoint (Cloudflare Worker + D1).
//
// Since the free edition (4.9) the installer is streamed to anyone at
// GET /installer, and POST /subscribe records an optional email for release
// news. GET /installer-info says whether the installer currently in R2 still
// asks for an activation code on first start (a build from before 4.9) and,
// if so, the shared code the site should show. The code-gated routes
// (/activate, /download, /file) stay live for installs activated under the
// paid model; the app runs fully offline after activation, and the free build
// never calls them.
//
// It holds no private key and signs nothing: signatures are minted offline and
// stored at seed time. The app verifies them against the embedded public key,
// so this server cannot forge entitlements even if fully compromised.

// The website calls /download from a different origin (the site is on Pages,
// this is on workers.dev), and it sends Content-Type: application/json, which
// makes it a non-simple request: the browser sends an OPTIONS preflight first
// and refuses to expose the response without Access-Control-Allow-Origin.
//
// Without this the download form failed for every buyer holding a valid code,
// landing in its "could not reach the server" branch — indistinguishable, when
// testing against an endpoint that did not exist yet, from the expected error.
//
// Set ALLOWED_ORIGIN to the published site to narrow it. "*" is the default and
// is safe here: both endpoints require a valid code to return anything, no
// credentials are accepted, and nothing is readable that the caller did not
// already supply.
function corsHeaders(env) {
  return {
    "access-control-allow-origin": (env && env.ALLOWED_ORIGIN) || "*",
    "access-control-allow-methods": "POST, GET, OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-max-age": "86400",
  };
}

function json(body, status = 200, env = null) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...corsHeaders(env) },
  });
}

function err(code, message, status, env = null) {
  return json({ code, message }, status, env);
}

// The installer lives in a PRIVATE R2 bucket and is streamed by this Worker,
// never linked to directly.
//
// The alternative was enabling public access on the bucket and handing out its
// r2.dev URL. That URL is permanent and unauthenticated, so the first buyer to
// post it anywhere would make the code gate meaningless. Streaming it here
// means the file can only be fetched with a code that is still in the
// database, checked on the file request itself rather than only on the form.
const INSTALLER_KEY = "PitWall-Setup.exe";
const ANDROID_KEY = "YourPitBox-4.14.0-android.28.apk";

// Count starts only after R2 supplies a successful response. No per-visitor
// data is stored. HEAD, prefetch and all Range requests are deliberately
// excluded: retries/chunked resumes must not inflate the headline number.
// Full GET retries still count again; this is not a unique-user/install count.
async function countDownloadStart(request, response, env, ctx, platform) {
  response.headers.set("cache-control", "private, no-store");
  const purpose = `${request.headers.get("purpose") || ""} ${request.headers.get("sec-purpose") || ""}`;
  if (request.method !== "GET" || response.status !== 200 ||
      request.headers.has("range") || /prefetch/i.test(purpose)) return response;
  const record = async () => {
    const stamp = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO download_daily (day, platform, starts, first_started_at, last_started_at)
       VALUES (?, ?, 1, ?, ?)
       ON CONFLICT(day, platform) DO UPDATE SET
         starts = download_daily.starts + 1,
         first_started_at = MIN(download_daily.first_started_at, excluded.first_started_at),
         last_started_at = MAX(download_daily.last_started_at, excluded.last_started_at)`
    ).bind(stamp.slice(0, 10), platform, stamp, stamp).run();
  };
  // Analytics must never prevent or buffer a download. Log only a fixed
  // diagnostic, not request URLs, headers, visitor data or credentials.
  const pending = record().catch(() => console.warn("Download count could not be recorded"));
  if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(pending);
  else await pending;
  return response;
}

function reportJson(body, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "private, no-store",
      "access-control-allow-origin": "https://yourpitbox.com",
      "access-control-allow-methods": "GET, OPTIONS",
      "access-control-allow-headers": "authorization",
      "vary": "Origin",
      "x-content-type-options": "nosniff",
    },
  });
}

async function validReportToken(request, env) {
  return validPrivateToken(request, env.DOWNLOAD_REPORT_TOKEN);
}

async function validPrivateToken(request, expected) {
  const supplied = request.headers.get("authorization") || "";
  if (typeof expected !== "string" || expected.length < 32 || supplied.length > 512) return false;
  // Fixed-length digest comparison avoids comparing a secret prefix directly.
  const encoder = new TextEncoder();
  const hashes = await Promise.all([supplied, `Bearer ${expected}`].map(
    value => crypto.subtle.digest("SHA-256", encoder.encode(value))
  ));
  const left = new Uint8Array(hashes[0]), right = new Uint8Array(hashes[1]);
  let difference = 0;
  for (let i = 0; i < left.length; i++) difference |= left[i] ^ right[i];
  return difference === 0;
}

function readDownloadHistory(row) {
  if (!row) return null;
  const history = JSON.parse(row.report_json);
  const validCount = value => Number.isSafeInteger(value) && value >= 0;
  if (history.metric !== "historical_file_requests" || history.source !== "Cloudflare R2 analytics" ||
      ![history.from, history.until, history.recovered_at].every(value => typeof value === "string" && Number.isFinite(Date.parse(value))) ||
      Date.parse(history.from) >= Date.parse(history.until) ||
      !history.totals || ![history.totals.windows, history.totals.android, history.totals.all].every(validCount) ||
      !Array.isArray(history.daily) || history.daily.length > 730) throw new Error("Invalid historical snapshot");
  const totals = {windows: 0, android: 0, all: 0}, seen = new Set();
  for (const entry of history.daily) {
    const key = `${entry.day}/${entry.platform}`;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(entry.day) ||
        entry.day < history.from.slice(0, 10) || entry.day > history.until.slice(0, 10) ||
        !["windows", "android"].includes(entry.platform) || !validCount(entry.requests) || seen.has(key)) {
      throw new Error("Invalid historical row");
    }
    seen.add(key);
    totals[entry.platform] += entry.requests;
    totals.all += entry.requests;
  }
  if (Object.keys(totals).some(key => !validCount(totals[key]) || totals[key] !== history.totals[key])) {
    throw new Error("Historical totals do not reconcile");
  }
  // Return only aggregate reporting fields, not arbitrary import metadata.
  return {metric: history.metric, source: history.source, from: history.from,
    until: history.until, recovered_at: history.recovered_at, totals, daily: history.daily};
}

async function handleDownloadReport(request, env) {
  if (!(await validReportToken(request, env))) {
    return reportJson({code: "unauthorized", message: "Enter your download-report access key."}, 401);
  }
  return readDownloadReport(env);
}

async function readDownloadReport(env) {
  try {
    const now = new Date();
    const since = new Date(now.getTime() - 29 * 86400000).toISOString().slice(0, 10);
    const results = await env.DB.batch([
      env.DB.prepare(`SELECT platform, SUM(starts) AS starts,
        MIN(first_started_at) AS first_started_at, MAX(last_started_at) AS last_started_at
        FROM download_daily GROUP BY platform`),
      env.DB.prepare(`SELECT day, platform, starts FROM download_daily
        WHERE day >= ? ORDER BY day DESC, platform`).bind(since),
      env.DB.prepare(`SELECT report_json FROM download_history WHERE id = 1`),
    ]);
    if (results.some(result => !result.success)) throw new Error("Report query failed");
    const totals = {windows: 0, android: 0, all: 0};
    let first = null, last = null;
    for (const row of results[0].results) {
      if (!(row.platform === "windows" || row.platform === "android")) continue;
      totals[row.platform] = Number(row.starts);
      totals.all += Number(row.starts);
      if (!first || row.first_started_at < first) first = row.first_started_at;
      if (!last || row.last_started_at > last) last = row.last_started_at;
    }
    return reportJson({
      metric: "download_starts", generated_at: now.toISOString(),
      first_recorded_at: first, last_recorded_at: last, totals,
      daily_since: since, daily: results[1].results,
      history: readDownloadHistory(results[2].results[0]),
    });
  } catch {
    console.warn("Download report unavailable");
    return reportJson({code: "unavailable", message: "Counts are temporarily unavailable. Try again."}, 503);
  }
}

// A device hash is a 64-char lowercase hex SHA-256.
function validDeviceHash(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

// Canonical code shape: PITW-XXXXX-XXXXX-XXXXX in Crockford base32.
// Must match dist/licensing/codes.py::normalize_code exactly.
function normalizeCode(raw) {
  if (typeof raw !== "string") return null;
  let text = raw.trim().toUpperCase().replace(/[\s_]/g, "");
  // Strip the PITW prefix BEFORE Crockford substitution: "PITW" contains an
  // "I", which the I->1 rule would otherwise corrupt into "P1TW".
  text = text.replace(/^PITW-?/, "");
  const body = text.replace(/-/g, "").replace(/O/g, "0").replace(/[IL]/g, "1");
  if (!/^[0-9A-HJKMNP-TV-Z]{15}$/.test(body)) return null;
  return `PITW-${body.slice(0, 5)}-${body.slice(5, 10)}-${body.slice(10, 15)}`;
}

function entitlementResponse(row, env) {
  return json(
    {
      entitlement: JSON.parse(row.entitlement_json),
      signature: row.signature,
    },
    200,
    env
  );
}

// Small key/value table (migrations/0003_settings.sql). Two keys matter:
//
//   installer_needs_code  "1" while the installer in R2 is a build from before
//                         the free edition, which still asks for an activation
//                         code on first start. The site shows the shared code
//                         while this is "1"; the release script sets it to "0"
//                         right after it uploads a free-edition installer.
//   universal_code        one seeded code reserved as the shared code. /activate
//                         returns its entitlement to every device without
//                         claiming it, so the pre-4.9 build activates for anyone.
//
// Both are read on demand and absent means "off", so a database without the
// table behaves exactly as before.
async function readSetting(env, key) {
  try {
    const row = await env.DB.prepare("SELECT value FROM settings WHERE key = ?")
      .bind(key)
      .first();
    return row ? String(row.value) : null;
  } catch {
    return null;
  }
}

async function handleInstallerInfo(request, env) {
  const needsCode = (await readSetting(env, "installer_needs_code")) === "1";
  const code = needsCode ? await readSetting(env, "universal_code") : null;
  return json({ needs_code: needsCode && Boolean(code), code: code || null }, 200, env);
}

async function handleActivate(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return err("bad_request", "Body must be JSON.", 400, env);
  }

  const codeId = normalizeCode(payload && payload.code);
  const deviceHash = payload && payload.device_hash;
  if (!codeId) return err("code_not_found", "Code not recognized.", 404, env);
  if (!validDeviceHash(deviceHash)) {
    return err("bad_request", "Missing or malformed device hash.", 400, env);
  }

  const db = env.DB;

  // The shared code of the free edition's bridge period: every device gets the
  // same pre-signed entitlement and nothing is claimed. The app only checks
  // the signature and binds the licence to its own machine locally, so one
  // entitlement serves any number of installs. Checked before the claimed and
  // disabled branches on purpose: the reserved row is marked claimed so the
  // ordinary path can never hand it to anyone.
  const universal = await readSetting(env, "universal_code");
  if (universal && codeId === universal) {
    const shared = await db
      .prepare("SELECT entitlement_json, signature FROM codes WHERE code_id = ?")
      .bind(codeId)
      .first();
    if (shared) return entitlementResponse(shared, env);
  }

  const existing = await db
    .prepare(
      "SELECT entitlement_json, signature, claimed, claimed_device, disabled FROM codes WHERE code_id = ?"
    )
    .bind(codeId)
    .first();

  if (!existing) return err("code_not_found", "Code not recognized.", 404, env);

  // Retired by the ledger sync (Replaced / Void in the workbook). Checked
  // before the claimed branch on purpose: a Replaced code's original device
  // must not keep re-activating alongside its replacement — that is the very
  // hole the sync exists to close. Running installs are unaffected; they
  // validate their cached licence offline.
  if (existing.disabled === 1) {
    return err(
      "code_retired",
      "This code has been retired. Reply to your purchase email and I will sort it out.",
      410,
      env
    );
  }

  // Already claimed: same device is a re-install (allowed); any other device is
  // refused. This is the single-global-use rule.
  if (existing.claimed === 1) {
    if (existing.claimed_device === deviceHash) return entitlementResponse(existing, env);
    return err(
      "code_already_claimed",
      "This code has already been activated on another device.",
      409,
      env
    );
  }

  // Atomic claim: only succeeds if the row is still unclaimed. D1 is strongly
  // consistent, so exactly one concurrent request can flip claimed 0 -> 1.
  const claimedAt = new Date().toISOString();
  const result = await db
    .prepare(
      "UPDATE codes SET claimed = 1, claimed_device = ?, claimed_at = ? WHERE code_id = ? AND claimed = 0"
    )
    .bind(deviceHash, claimedAt, codeId)
    .run();

  if (result.meta.changes === 1) return entitlementResponse(existing, env);

  // Lost a race: re-read and honor an identical-device claim, else refuse.
  const now = await db
    .prepare("SELECT entitlement_json, signature, claimed_device FROM codes WHERE code_id = ?")
    .bind(codeId)
    .first();
  if (now && now.claimed_device === deviceHash) return entitlementResponse(now, env);
  return err(
    "code_already_claimed",
    "This code has already been activated on another device.",
    409,
    env
  );
}

// Gate the installer download on a real code.
//
// Deliberately does NOT claim the code. Claiming happens once, at activation,
// bound to a device. If downloading burned the code, a buyer whose disk died
// mid-install would be locked out of the file they had paid for, and every
// support request would cost a replacement code.
//
// Be honest about what this is: a funnel, not a security boundary. Anyone who
// has downloaded the installer can pass the file to someone else — it is the
// activation code that makes a copy usable, and that is enforced server-side
// and re-checked on every launch. This stops the download being a public link,
// nothing more.
async function handleDownload(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return err("bad_request", "Body must be JSON.", 400, env);
  }

  const codeId = normalizeCode(payload && payload.code);
  if (!codeId) {
    return err(
      "code_not_found",
      "That does not look like a Pit Wall activation code. It has the form PITW-XXXXX-XXXXX-XXXXX.",
      404,
      env
    );
  }

  const row = await env.DB.prepare("SELECT code_id, disabled FROM codes WHERE code_id = ?")
    .bind(codeId)
    .first();
  if (!row) {
    return err(
      "code_not_found",
      "That code was not recognized. Check it against your purchase email, or reply to it and I will sort it out.",
      404,
      env
    );
  }
  if (row.disabled === 1) {
    return err(
      "code_retired",
      "This code has been retired. Reply to your purchase email and I will sort it out.",
      410,
      env
    );
  }

  // Prefer streaming from R2 through this Worker. DOWNLOAD_URL remains as an
  // override for hosting the file somewhere else entirely.
  if (env.DOWNLOADS) {
    const origin = new URL(request.url).origin;
    return json(
      {
        url: `${origin}/file?code=${encodeURIComponent(codeId)}`,
        filename: INSTALLER_KEY,
      },
      200,
      env
    );
  }

  const target = env.DOWNLOAD_URL;
  if (!target) {
    return err(
      "not_configured",
      "The download is not available yet. Email vale.scott00@gmail.com and I will send it directly.",
      503,
      env
    );
  }
  return json({ url: target, filename: INSTALLER_KEY }, 200, env);
}

// Serve the installer itself.
//
// A GET rather than a POST because the page navigates to it: that gives the
// buyer the browser's own download UI and progress bar, and Range support so a
// dropped connection resumes instead of restarting 33 MB.
//
// The code travels in the query string, which means it lands in the buyer's
// browser history. That is the deliberate trade: the link is only useful to
// someone holding a real code, and passing the link on means passing on your
// own activation code, which is the thing you actually paid for.
async function handleFile(request, env, url) {
  const codeId = normalizeCode(url.searchParams.get("code"));
  if (!codeId) {
    return err("code_not_found", "Code not recognized.", 404, env);
  }

  const row = await env.DB.prepare("SELECT code_id, disabled FROM codes WHERE code_id = ?")
    .bind(codeId)
    .first();
  if (!row) {
    return err("code_not_found", "That code was not recognized.", 404, env);
  }
  if (row.disabled === 1) {
    return err("code_retired", "This code has been retired.", 410, env);
  }

  return streamInstaller(request, env);
}

// Stream the installer from the private bucket, with Range support so a
// dropped connection resumes instead of restarting 33 MB.
async function streamInstaller(request, env, objectKey = INSTALLER_KEY) {
  if (!env.DOWNLOADS) {
    return err("not_configured", "The download is not available yet. Email vale.scott00@gmail.com and I will send it directly.", 503, env);
  }

  // HEAD answers with the object's metadata and no body, so the release
  // script can confirm the Worker is serving the installer without pulling
  // 33 MB through wrangler's (known stale) reader.
  if (request.method === "HEAD") {
    const meta = await env.DOWNLOADS.head(objectKey);
    if (!meta) {
      return err("not_configured", "The installer is not uploaded yet.", 503, env);
    }
    const headHeaders = new Headers(corsHeaders(env));
    meta.writeHttpMetadata(headHeaders);
    headHeaders.set("etag", meta.httpEtag);
    headHeaders.set("content-disposition", `attachment; filename="${objectKey}"`);
    headHeaders.set("accept-ranges", "bytes");
    headHeaders.set("content-length", String(meta.size));
    return new Response(null, { status: 200, headers: headHeaders });
  }

  // Only ask R2 for a range when one was actually requested. Passing the
  // headers unconditionally makes it populate object.range even for an
  // ordinary GET, which then answers a plain download with 206 Partial
  // Content — wrong, and enough to confuse download managers.
  const rangeHeader = request.headers.get("range");
  const object = await env.DOWNLOADS.get(
    objectKey,
    rangeHeader ? { range: request.headers } : undefined
  );
  if (!object) {
    return err(
      "not_configured",
      "The installer is not uploaded yet. Email vale.scott00@gmail.com and I will send it directly.",
      503,
      env
    );
  }

  const headers = new Headers(corsHeaders(env));
  object.writeHttpMetadata(headers);
  headers.set("etag", object.httpEtag);
  headers.set("content-disposition", `attachment; filename="${objectKey}"`);
  headers.set("accept-ranges", "bytes");
  // Keep the code out of the Referer sent to anywhere the buyer clicks next.
  headers.set("referrer-policy", "no-referrer");

  if (rangeHeader && object.range && typeof object.range.offset === "number") {
    const end = object.range.offset + object.range.length - 1;
    headers.set("content-range", `bytes ${object.range.offset}-${end}/${object.size}`);
    headers.set("content-length", String(object.range.length));
    return new Response(object.body, { status: 206, headers });
  }
  headers.set("content-length", String(object.size));
  return new Response(object.body, { status: 200, headers });
}

// The free edition: no code, no form. The page's Download button lands here
// (after the optional email prompt), and so does anyone who copies the link —
// that is the point now, not a hole.
async function handleInstaller(request, env) {
  return streamInstaller(request, env);
}

// Optional release-news signup from the download prompt. Stores the address
// and when it arrived, nothing else; duplicates are silently kept once.
const EMAIL_SHAPE = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,24}$/;

async function handleSubscribe(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return err("bad_request", "Body must be JSON.", 400, env);
  }
  const raw = payload && payload.email;
  const email = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if (!email || email.length > 254 || !EMAIL_SHAPE.test(email)) {
    return err("bad_email", "That does not look like an email address.", 422, env);
  }
  const source = typeof payload.source === "string" ? payload.source.slice(0, 40) : "website";
  try {
    await env.DB.prepare(
      "INSERT OR IGNORE INTO subscribers (email, created_at, source) VALUES (?, ?, ?)"
    )
      .bind(email, new Date().toISOString(), source)
      .run();
  } catch (e) {
    // The subscribers table comes from migrations/0002_subscribers.sql; until
    // it exists the download must still work, so this is a soft failure.
    return err("not_ready", "The mailing list is not set up yet, but your download will still start.", 503, env);
  }
  return json({ ok: true }, 200, env);
}

// Reviews from the website. Anyone can post one; nothing is shown until the
// owner has read it and set approved = 1 by hand in D1 (see
// migrations/0004_reviews.sql). The optional email is for a reply and is
// never returned to the page.
const REVIEW_NAME_MAX = 60;
const REVIEW_BODY_MIN = 20;
const REVIEW_BODY_MAX = 1200;
const REVIEWS_PER_DAY_PER_ADDRESS = 3;

// A truncated one-way hash of the posting address, so one connection cannot
// flood the queue. Not reversible, and not the address itself.
async function addressHash(request) {
  const address = request.headers.get("cf-connecting-ip") || "";
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(`pitwall-reviews:${address}`)
  );
  return [...new Uint8Array(digest)]
    .slice(0, 12)
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function handleReviewsList(request, env) {
  let rows = [];
  try {
    const result = await env.DB.prepare(
      "SELECT name, rating, body, created_at FROM reviews WHERE approved = 1 ORDER BY created_at DESC LIMIT 50"
    ).all();
    rows = result.results || [];
  } catch {
    // Until migrations/0004_reviews.sql is applied the page simply shows
    // its empty state.
    rows = [];
  }
  const count = rows.length;
  const average = count
    ? Math.round((rows.reduce((sum, row) => sum + Number(row.rating), 0) / count) * 10) / 10
    : null;
  const response = json({ reviews: rows, count, average }, 200, env);
  response.headers.set("cache-control", "public, max-age=60");
  return response;
}

async function handleReviewSubmit(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return err("bad_request", "Body must be JSON.", 400, env);
  }
  const text = (value, max) => (typeof value === "string" ? value.trim().slice(0, max) : "");

  // Honeypot: the form's "website" field is hidden from people and left
  // empty; a bot fills it. Answer as if stored so it cannot tell.
  if (text(payload && payload.website, 10)) return json({ ok: true, pending: true }, 200, env);

  const name = text(payload && payload.name, REVIEW_NAME_MAX);
  const body = text(payload && payload.body, REVIEW_BODY_MAX + 1);
  const rating = Number(payload && payload.rating);
  const email = text(payload && payload.email, 254).toLowerCase();

  if (!name) return err("bad_review", "Add the name you want shown.", 422, env);
  if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
    return err("bad_review", "Pick a rating from 1 to 5.", 422, env);
  }
  if (body.length < REVIEW_BODY_MIN) {
    return err("bad_review", `Say a little more: at least ${REVIEW_BODY_MIN} characters.`, 422, env);
  }
  if (body.length > REVIEW_BODY_MAX) {
    return err("bad_review", `Keep it under ${REVIEW_BODY_MAX} characters.`, 422, env);
  }
  if (email && !EMAIL_SHAPE.test(email)) {
    return err("bad_review", "That does not look like an email address. Leave it blank if you prefer.", 422, env);
  }

  const hash = await addressHash(request);
  const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  try {
    const recent = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM reviews WHERE address_hash = ? AND created_at > ?"
    )
      .bind(hash, since)
      .first();
    if (recent && Number(recent.n) >= REVIEWS_PER_DAY_PER_ADDRESS) {
      return err("too_many", "That is enough reviews from one connection for today. Thank you, though.", 429, env);
    }
    await env.DB.prepare(
      "INSERT INTO reviews (name, rating, body, email, created_at, address_hash, approved) VALUES (?, ?, ?, ?, ?, ?, 0)"
    )
      .bind(name, rating, body, email || null, new Date().toISOString(), hash)
      .run();
  } catch (e) {
    return err(
      "not_ready",
      "Reviews are not set up yet. Email vale.scott00@gmail.com instead and I will post it for you.",
      503,
      env
    );
  }
  return json({ ok: true, pending: true }, 200, env);
}

// Optional app usage. Only daily yes/no flags, with an explicit consent version.
// Installation IDs are random/resettable, hashed before storage, never returned
// by the owner report, and never joined to subscriber emails or download logs.
const USAGE_EVENTS = new Set(["app_started", "app_used", "racing", "engineer", "voice", "analysis", "transfer"]);
const dayBefore = (days, now = Date.now()) => new Date(now - days * 86400000).toISOString().slice(0, 10);
const exactKeys = (object, keys) => object && typeof object === "object" && !Array.isArray(object) &&
  Object.keys(object).length === keys.length && keys.every(key => Object.hasOwn(object, key));

function usageJson(body, status = 200) {
  // Native backend calls only: don't enable cross-origin browser ingestion.
  return new Response(JSON.stringify(body), {status, headers: {
    "content-type": "application/json", "cache-control": "private, no-store",
    "x-content-type-options": "nosniff",
    ...(status === 429 ? {"retry-after": "60"} : {}),
  }});
}

async function readUsageBody(request) {
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") return null;
  if (Number(request.headers.get("content-length") || 0) > 8192 || !request.body) return null;
  const reader = request.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > 8192) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const value of chunks) { bytes.set(value, offset); offset += value.byteLength; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); } catch { return null; }
}

async function handleUsage(request, env) {
  try {
    // Fail closed if the rate-limit binding was not configured at deployment.
    if (!env.USAGE_RATE_LIMITER) return usageJson({code: "unavailable"}, 503);
    const limit = await env.USAGE_RATE_LIMITER.limit({key: request.headers.get("cf-connecting-ip") || "unknown"});
    if (!limit.success) return usageJson({code: "rate_limited"}, 429);
    const data = await readUsageBody(request);
    if (!exactKeys(data, ["consent_version", "installation_id", "platform", "version", "events"]) ||
        data.consent_version !== 1 || !["windows", "android"].includes(data.platform) ||
        typeof data.installation_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(data.installation_id) ||
        typeof data.version !== "string" || data.version.length > 40 || !/^\d+\.\d+\.\d+(?:[.-][a-zA-Z0-9]+)*$/.test(data.version) ||
        !Array.isArray(data.events) || !data.events.length || data.events.length > 28) {
      return usageJson({code: "invalid_report"}, 400);
    }
    const today = dayBefore(0), cutoff = dayBefore(6);
    for (const item of data.events) {
      if (!exactKeys(item, ["day", "event"]) || typeof item.day !== "string" ||
          !/^\d{4}-\d{2}-\d{2}$/.test(item.day) || item.day < cutoff || item.day > today ||
          !Number.isFinite(Date.parse(item.day)) || new Date(item.day).toISOString().slice(0, 10) !== item.day ||
          !USAGE_EVENTS.has(item.event)) return usageJson({code: "invalid_report"}, 400);
    }
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data.installation_id));
    const identity = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    const existing = await env.DB.prepare("SELECT platform FROM usage_installations WHERE installation_hash = ?").bind(identity).first();
    if (existing && existing.platform !== data.platform) return usageJson({code: "platform_mismatch"}, 409);
    const days = data.events.map(item => item.day).sort();
    const statements = [env.DB.prepare(`INSERT INTO usage_installations
      (installation_hash, platform, version, first_day, last_day, consent_version) VALUES (?, ?, ?, ?, ?, 1)
      ON CONFLICT(installation_hash) DO UPDATE SET
        version = CASE WHEN excluded.last_day >= usage_installations.last_day THEN excluded.version ELSE usage_installations.version END,
        first_day = MIN(usage_installations.first_day, excluded.first_day),
        last_day = MAX(usage_installations.last_day, excluded.last_day)`)
      .bind(identity, data.platform, data.version, days[0], days.at(-1))];
    for (const item of data.events) statements.push(env.DB.prepare(
      "INSERT OR IGNORE INTO usage_daily (installation_hash, day, event) VALUES (?, ?, ?)"
    ).bind(identity, item.day, item.event));
    const results = await env.DB.batch(statements);
    if (results.some(result => !result.success)) throw new Error("Usage write failed");
    return usageJson({ok: true});
  } catch {
    console.warn("Optional usage report unavailable");
    return usageJson({code: "unavailable"}, 503);
  }
}

async function purgeUsage(env) {
  const cutoff = dayBefore(89);
  // Separate prepared statements; D1 batch is atomic.
  await env.DB.batch([
    env.DB.prepare("DELETE FROM usage_daily WHERE day < ?").bind(cutoff),
    env.DB.prepare("DELETE FROM usage_installations WHERE last_day < ?").bind(cutoff),
  ]);
}

async function handleUsageReport(request, env) {
  if (!(await validReportToken(request, env))) return reportJson({code: "unauthorized"}, 401);
  return readUsageReport(env);
}

async function readUsageReport(env) {
  try {
    const today = dayBefore(0), cutoff = dayBefore(89);
    const result = await env.DB.batch([
      env.DB.prepare(`SELECT platform, COUNT(*) AS installations,
        SUM(first_day >= ?) AS new_30d FROM usage_installations WHERE last_day >= ? GROUP BY platform`).bind(dayBefore(29), cutoff),
      env.DB.prepare(`SELECT i.platform, d.event, COUNT(DISTINCT d.installation_hash) AS installations,
        COUNT(*) AS active_days FROM usage_daily d JOIN usage_installations i USING(installation_hash)
        WHERE d.day >= ? GROUP BY i.platform, d.event`).bind(dayBefore(29)),
      env.DB.prepare(`SELECT i.platform,
        COUNT(DISTINCT CASE WHEN d.day >= ? THEN d.installation_hash END) AS active_7d,
        COUNT(DISTINCT CASE WHEN d.day = ? THEN d.installation_hash END) AS active_today
        FROM usage_daily d JOIN usage_installations i USING(installation_hash)
        WHERE d.event = 'app_used' AND d.day >= ? GROUP BY i.platform`).bind(dayBefore(6), today, dayBefore(6)),
      env.DB.prepare(`SELECT day, event, i.platform, COUNT(*) AS installations
        FROM usage_daily d JOIN usage_installations i USING(installation_hash)
        WHERE day >= ? GROUP BY day, event, i.platform ORDER BY day DESC`).bind(dayBefore(29)),
      env.DB.prepare(`SELECT platform, version, COUNT(*) AS installations FROM usage_installations
        WHERE last_day >= ? GROUP BY platform, version ORDER BY installations DESC LIMIT 50`).bind(dayBefore(29)),
      ...[1, 7, 30].map(days => env.DB.prepare(`SELECT i.platform, COUNT(*) AS eligible,
        SUM(EXISTS(SELECT 1 FROM usage_daily d WHERE d.installation_hash = i.installation_hash
          AND d.event = 'app_used' AND d.day = date(i.first_day, ?))) AS returned
        FROM usage_installations i WHERE i.first_day >= ? AND i.first_day <= ?
        GROUP BY i.platform`).bind(`+${days} days`, cutoff, dayBefore(days))),
    ]);
    if (result.some(item => !item.success)) throw new Error("Usage report failed");
    return reportJson({metric: "opted_in_installations", generated_at: new Date().toISOString(),
      since: cutoff, activity_since: dayBefore(29), installations: result[0].results,
      features: result[1].results, recent: result[2].results, daily: result[3].results,
      versions: result[4].results,
      retention: [1, 7, 30].map((day, index) => ({day, platforms: result[5 + index].results})),
    });
  } catch {
    console.warn("Usage dashboard unavailable");
    return reportJson({code: "unavailable"}, 503);
  }
}

// The owner key is deliberately separate: an aggregate-report key must never
// gain access to newsletter email addresses. No owner route changes user data.
const OWNER_ORIGINS = new Set(["https://yourpitbox.com", "https://www.yourpitbox.com"]);
function ownerJson(request, body, status = 200) {
  const origin = request.headers.get("origin");
  return new Response(status === 204 ? null : JSON.stringify(body), {status, headers: {
    "content-type": "application/json", "cache-control": "private, no-store",
    "x-content-type-options": "nosniff", "x-robots-tag": "noindex, nofollow, noarchive",
    "referrer-policy": "no-referrer", "vary": "Origin",
    ...(OWNER_ORIGINS.has(origin) ? {"access-control-allow-origin": origin,
      "access-control-allow-methods": "GET, OPTIONS", "access-control-allow-headers": "authorization"} : {}),
    ...(status === 429 ? {"retry-after": "60"} : {}),
  }});
}

async function readSubscriberSummary(env) {
  const since = dayBefore(29);
  const results = await env.DB.batch([
    env.DB.prepare("SELECT COUNT(*) AS total, COALESCE(SUM(created_at >= ?), 0) AS new_30d FROM subscribers").bind(since),
    env.DB.prepare("SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS total FROM subscribers GROUP BY source ORDER BY total DESC LIMIT 50"),
    env.DB.prepare("SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS signups FROM subscribers WHERE created_at >= ? GROUP BY day ORDER BY day DESC").bind(since),
  ]);
  if (results.some(result => !result.success)) throw new Error("Subscriber summary unavailable");
  return {metric: "newsletter_subscribers", total: Number(results[0].results[0].total),
    new_30d: Number(results[0].results[0].new_30d), daily_since: since,
    sources: results[1].results, daily: results[2].results};
}

async function handleOwner(request, env, path) {
  const origin = request.headers.get("origin");
  if (origin && !OWNER_ORIGINS.has(origin)) return ownerJson(request, {code: "forbidden_origin"}, 403);
  if (request.method === "OPTIONS") return ownerJson(request, null, 204);
  if (request.method !== "GET") return ownerJson(request, {code: "method_not_allowed"}, 405);
  try {
    if (!env.OWNER_RATE_LIMITER) return ownerJson(request, {code: "unavailable"}, 503);
    const limited = await env.OWNER_RATE_LIMITER.limit({key: request.headers.get("cf-connecting-ip") || "unknown"});
    if (!limited.success) return ownerJson(request, {code: "rate_limited"}, 429);
    if (env.OWNER_DASHBOARD_TOKEN === env.DOWNLOAD_REPORT_TOKEN ||
        !(await validPrivateToken(request, env.OWNER_DASHBOARD_TOKEN))) {
      return ownerJson(request, {code: "unauthorized"}, 401);
    }
    const params = new URL(request.url).searchParams;
    if (path === "/owner/overview") {
      if ([...params].length) return ownerJson(request, {code: "invalid_query"}, 400);
      const [downloadResponse, usageResponse, subscribers] = await Promise.all([
        readDownloadReport(env), readUsageReport(env), readSubscriberSummary(env).catch(() => null),
      ]);
      return ownerJson(request, {generated_at: new Date().toISOString(),
        downloads: downloadResponse.ok ? await downloadResponse.json() : null,
        usage: usageResponse.ok ? await usageResponse.json() : null,
        subscribers,
      });
    }
    if (path !== "/owner/subscribers") return ownerJson(request, {code: "not_found"}, 404);
    if ([...params.keys()].some(key => !["limit", "snapshot", "before"].includes(key)) ||
        [...params.keys()].some(key => params.getAll(key).length !== 1)) {
      return ownerJson(request, {code: "invalid_query"}, 400);
    }
    const integer = (key, fallback) => {
      if (!params.has(key)) return fallback;
      const value = params.get(key);
      return /^\d{1,16}$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : NaN;
    };
    const limit = integer("limit", 100), suppliedSnapshot = integer("snapshot", null), before = integer("before", null);
    if (!Number.isInteger(limit) || limit < 1 || limit > 200 ||
        (suppliedSnapshot !== null && (!Number.isSafeInteger(suppliedSnapshot) || suppliedSnapshot < 0)) ||
        (before !== null && (!Number.isSafeInteger(before) || before <= 0 || suppliedSnapshot === null || before > suppliedSnapshot))) {
      return ownerJson(request, {code: "invalid_query"}, 400);
    }
    // Integer row cursors keep private email addresses out of URLs and access logs.
    // A fixed high-water row prevents new signups shifting pages during Copy all.
    const snapshot = suppliedSnapshot ?? Number((await env.DB.prepare("SELECT COALESCE(MAX(rowid), 0) AS snapshot FROM subscribers").first()).snapshot);
    const results = await env.DB.batch([
      env.DB.prepare("SELECT rowid AS id, email, created_at, source FROM subscribers WHERE rowid <= ? AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT ?")
        .bind(snapshot, before, before, limit + 1),
      env.DB.prepare("SELECT COUNT(*) AS total FROM subscribers WHERE rowid <= ?").bind(snapshot),
    ]);
    if (results.some(result => !result.success)) throw new Error("Subscriber page unavailable");
    const rows = results[0].results, page = rows.slice(0, limit);
    return ownerJson(request, {generated_at: new Date().toISOString(), snapshot,
      total: Number(results[1].results[0].total), next: rows.length > limit ? Number(page.at(-1).id) : null,
      subscribers: page.map(({email, created_at, source}) => ({email, created_at, source})),
    });
  } catch {
    // Never log addresses, credentials, request headers or database response bodies.
    console.warn("Owner dashboard unavailable");
    return ownerJson(request, {code: "unavailable"}, 503);
  }
}

const RELEASE_VERSION = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const releaseUrl = platform => `https://yourpitbox.com/#${platform}-release`;
async function currentRelease(env, platform) {
  return env.DB.prepare("SELECT r.* FROM app_releases r JOIN release_channels c ON c.release_id = r.id WHERE c.platform = ?").bind(platform).first();
}
function publicRelease(row) {
  return row ? {version: row.version, published_at: row.published_at, notes: row.notes, sha256: row.sha256, size: row.size, download_url: releaseUrl(row.platform)} : null;
}
async function handleReleaseManifest(request, env) {
  if (request.method !== "GET") return new Response(null, {status: 405, headers: {Allow: "GET"}});
  const params = new URL(request.url).searchParams, platform = params.get("platform");
  if ([...params].length !== 1 || !["windows", "android"].includes(platform)) return json({code: "invalid_platform"}, 400);
  try {
    return new Response(JSON.stringify({schema_version: 1, platform, release: publicRelease(await currentRelease(env, platform))}), {headers: {"content-type": "application/json", "cache-control": "public, max-age=300", "x-content-type-options": "nosniff"}});
  } catch { return new Response(JSON.stringify({code: "unavailable"}), {status: 503, headers: {"content-type": "application/json", "cache-control": "no-store"}}); }
}
async function handleReleasePublish(request, env) {
  const reply = (body, status = 200) => {
    const result = ownerJson(request, body, status);
    if (OWNER_ORIGINS.has(request.headers.get("origin"))) {
      result.headers.set("access-control-allow-methods", "POST, OPTIONS");
      result.headers.set("access-control-allow-headers", "authorization, content-type");
    }
    return result;
  };
  const origin = request.headers.get("origin");
  if (origin && !OWNER_ORIGINS.has(origin)) return reply({code: "forbidden_origin"}, 403);
  if (request.method === "OPTIONS") return reply(null, 204);
  if (request.method !== "POST") return reply({code: "method_not_allowed"}, 405);
  try {
    if (!env.OWNER_RATE_LIMITER || !(await env.OWNER_RATE_LIMITER.limit({key: "release/" + (request.headers.get("cf-connecting-ip") || "unknown")})).success) return reply({code: "rate_limited"}, 429);
    if ([env.OWNER_DASHBOARD_TOKEN, env.DOWNLOAD_REPORT_TOKEN].includes(env.RELEASE_PUBLISH_TOKEN) || !(await validPrivateToken(request, env.RELEASE_PUBLISH_TOKEN))) return reply({code: "unauthorized"}, 401);
    if (new URL(request.url).search || !request.headers.get("content-type")?.startsWith("application/json")) return reply({code: "invalid_request"}, 400);
    const data = await readUsageBody(request);
    if (!data || Array.isArray(data)) return reply({code: "invalid_request"}, 400);
    if (Object.keys(data).some(k => !["platform", "version", "sha256", "size", "notes"].includes(k)) || !["windows", "android"].includes(data.platform) || typeof data.version !== "string" || !RELEASE_VERSION.test(data.version) || !/^[a-f0-9]{64}$/.test(data.sha256 || "") || !Number.isSafeInteger(data.size) || data.size < 1 || data.size > 2000000000 || typeof data.notes !== "string" || !data.notes.trim() || data.notes.length > 4000) return reply({code: "invalid_release"}, 400);
    const artifactKey = data.platform === "windows" ? INSTALLER_KEY : ANDROID_KEY;
    if (data.platform === "android" && !artifactKey.startsWith(`YourPitBox-${data.version}-android.`)) return reply({code: "artifact_version_mismatch"}, 409);
    const artifact = await env.DOWNLOADS.head(artifactKey);
    if (!artifact || artifact.size !== data.size || (artifact.customMetadata?.sha256 && artifact.customMetadata.sha256.toLowerCase() !== data.sha256)) return reply({code: "artifact_not_verified"}, 409);
    const id = `${data.platform}/${data.version}`, sortKey = data.version.split(".").map(n => n.padStart(6, "0")).join(".");
    const existing = await env.DB.prepare("SELECT * FROM app_releases WHERE id = ?").bind(id).first();
    if (existing && ["size", "sha256", "notes"].some(k => existing[k] !== data[k])) return reply({code: "immutable_release"}, 409);
    const current = await currentRelease(env, data.platform);
    if (current && current.sort_key > sortKey) return reply({code: "older_release"}, 409);
    const results = await env.DB.batch([
      env.DB.prepare("INSERT OR IGNORE INTO app_releases (id, platform, version, sort_key, artifact_key, size, sha256, notes, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)").bind(id, data.platform, data.version, sortKey, artifactKey, data.size, data.sha256, data.notes, new Date().toISOString()),
      env.DB.prepare("INSERT INTO release_channels (platform, release_id, sort_key) VALUES (?, ?, ?) ON CONFLICT(platform) DO UPDATE SET release_id = excluded.release_id, sort_key = excluded.sort_key WHERE release_channels.sort_key < excluded.sort_key").bind(data.platform, id, sortKey),
    ]);
    if (results.some(row => !row.success)) throw new Error("Release storage unavailable");
    const selected = await currentRelease(env, data.platform);
    if (selected?.id !== id) return reply({code: "superseded_release"}, 409);
    if (["size", "sha256", "notes"].some(k => selected[k] !== data[k])) return reply({code: "immutable_release"}, 409);
    return reply({ok: true, release: publicRelease(selected)});
  } catch { console.warn("Release operation unavailable"); return reply({code: "unavailable"}, 503); }
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(purgeUsage(env));
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/releases") return handleReleaseManifest(request, env);
    if (url.pathname === "/release-admin/publish") return handleReleasePublish(request, env);

    if (url.pathname.startsWith("/owner/")) return handleOwner(request, env, url.pathname);

    if (url.pathname === "/usage") {
      if (request.method !== "POST") return usageJson({code: "method_not_allowed"}, 405);
      return handleUsage(request, env);
    }
    if (url.pathname === "/usage-stats") {
      if (request.method === "OPTIONS") return reportJson(null, 204);
      if (request.method === "GET") return handleUsageReport(request, env);
      return reportJson({code: "method_not_allowed"}, 405);
    }

    if (url.pathname === "/download-stats") {
      if (request.method === "OPTIONS") return reportJson(null, 204);
      if (request.method === "GET") return handleDownloadReport(request, env);
      return reportJson({code: "method_not_allowed"}, 405);
    }

    // Preflight. Must answer before any POST from the website is even sent.
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true }, 200, env);
    }

    // The free edition: the site's Download button lands here.
    if ((request.method === "GET" || request.method === "HEAD") && url.pathname === "/installer") {
      try {
        const response = await handleInstaller(request, env);
        return await countDownloadStart(request, response, env, ctx, "windows");
      } catch (e) {
        return err("server_error", "Could not start the download. Try again.", 500, env);
      }
    }
    if (request.method === "GET" && url.pathname === "/installer-info") {
      return handleInstallerInfo(request, env);
    }
    // A pinned public APK only: never accept an arbitrary R2 key from a URL.
    if ((request.method === "GET" || request.method === "HEAD") && url.pathname === "/android") {
      try {
        const response = await streamInstaller(request, env, ANDROID_KEY);
        return await countDownloadStart(request, response, env, ctx, "android");
      } catch (e) {
        return err("server_error", "Could not start the Android download. Try again.", 500, env);
      }
    }
    if (request.method === "GET" && url.pathname === "/reviews") {
      return handleReviewsList(request, env);
    }
    if (request.method === "POST" && url.pathname === "/reviews") {
      try {
        return await handleReviewSubmit(request, env);
      } catch (e) {
        return err("server_error", "Could not save that just now. Try again in a moment.", 500, env);
      }
    }
    if (request.method === "POST" && url.pathname === "/subscribe") {
      try {
        return await handleSubscribe(request, env);
      } catch (e) {
        return err("server_error", "Could not save that just now, but your download will still start.", 500, env);
      }
    }

    // Paid-model installs.
    if (request.method === "POST" && url.pathname === "/activate") {
      try {
        return await handleActivate(request, env);
      } catch (e) {
        return err("server_error", "Activation failed. Try again.", 500, env);
      }
    }
    if (request.method === "POST" && url.pathname === "/download") {
      try {
        return await handleDownload(request, env);
      } catch (e) {
        return err("server_error", "Could not check that code. Try again.", 500, env);
      }
    }
    if (request.method === "GET" && url.pathname === "/file") {
      try {
        return await handleFile(request, env, url);
      } catch (e) {
        return err("server_error", "Could not start the download. Try again.", 500, env);
      }
    }
    return err("not_found", "Not found.", 404, env);
  },
};
