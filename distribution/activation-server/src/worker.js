// Pit Wall download + activation endpoint (Cloudflare Worker + D1).
//
// Since the free edition (4.9) the installer is streamed to anyone at
// GET /installer, and POST /subscribe records an optional email for release
// news. The code-gated routes (/activate, /download, /file) stay live for
// installs activated under the paid model; the app runs fully offline after
// activation, and the free build never calls them.
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
async function streamInstaller(request, env) {
  if (!env.DOWNLOADS) {
    return err("not_configured", "The download is not available yet. Email vale.scott00@gmail.com and I will send it directly.", 503, env);
  }

  // HEAD answers with the object's metadata and no body, so the release
  // script can confirm the Worker is serving the installer without pulling
  // 33 MB through wrangler's (known stale) reader.
  if (request.method === "HEAD") {
    const meta = await env.DOWNLOADS.head(INSTALLER_KEY);
    if (!meta) {
      return err("not_configured", "The installer is not uploaded yet.", 503, env);
    }
    const headHeaders = new Headers(corsHeaders(env));
    meta.writeHttpMetadata(headHeaders);
    headHeaders.set("etag", meta.httpEtag);
    headHeaders.set("content-disposition", `attachment; filename="${INSTALLER_KEY}"`);
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
    INSTALLER_KEY,
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
  headers.set("content-disposition", `attachment; filename="${INSTALLER_KEY}"`);
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

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
        return await handleInstaller(request, env);
      } catch (e) {
        return err("server_error", "Could not start the download. Try again.", 500, env);
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
