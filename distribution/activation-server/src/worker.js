// Pit Wall activation endpoint (Cloudflare Worker + D1).
//
// The ONE online interaction in the licensing system. It atomically claims a
// code for a device and returns the code's pre-signed entitlement. After this,
// the app runs fully offline.
//
// It holds no private key and signs nothing: signatures are minted offline and
// stored at seed time. The app verifies them against the embedded public key,
// so this server cannot forge entitlements even if fully compromised.

const JSON_HEADERS = { "content-type": "application/json" };

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function err(code, message, status) {
  return json({ code, message }, status);
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

function entitlementResponse(row) {
  return json({
    entitlement: JSON.parse(row.entitlement_json),
    signature: row.signature,
  });
}

async function handleActivate(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return err("bad_request", "Body must be JSON.", 400);
  }

  const codeId = normalizeCode(payload && payload.code);
  const deviceHash = payload && payload.device_hash;
  if (!codeId) return err("code_not_found", "Code not recognized.", 404);
  if (!validDeviceHash(deviceHash)) {
    return err("bad_request", "Missing or malformed device hash.", 400);
  }

  const db = env.DB;
  const existing = await db
    .prepare(
      "SELECT entitlement_json, signature, claimed, claimed_device FROM codes WHERE code_id = ?"
    )
    .bind(codeId)
    .first();

  if (!existing) return err("code_not_found", "Code not recognized.", 404);

  // Already claimed: same device is a re-install (allowed); any other device is
  // refused. This is the single-global-use rule.
  if (existing.claimed === 1) {
    if (existing.claimed_device === deviceHash) return entitlementResponse(existing);
    return err(
      "code_already_claimed",
      "This code has already been activated on another device.",
      409
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

  if (result.meta.changes === 1) return entitlementResponse(existing);

  // Lost a race: re-read and honor an identical-device claim, else refuse.
  const now = await db
    .prepare("SELECT entitlement_json, signature, claimed_device FROM codes WHERE code_id = ?")
    .bind(codeId)
    .first();
  if (now && now.claimed_device === deviceHash) return entitlementResponse(now);
  return err(
    "code_already_claimed",
    "This code has already been activated on another device.",
    409
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
    return err("bad_request", "Body must be JSON.", 400);
  }

  const codeId = normalizeCode(payload && payload.code);
  if (!codeId) {
    return err(
      "code_not_found",
      "That does not look like a Pit Wall activation code. It has the form PITW-XXXXX-XXXXX-XXXXX.",
      404
    );
  }

  const row = await env.DB.prepare("SELECT code_id FROM codes WHERE code_id = ?")
    .bind(codeId)
    .first();
  if (!row) {
    return err(
      "code_not_found",
      "That code was not recognized. Check it against your purchase email, or reply to it and I will sort it out.",
      404
    );
  }

  const target = env.DOWNLOAD_URL;
  if (!target) {
    return err(
      "not_configured",
      "The download is not available yet. Email vale.scott00@gmail.com and I will send it directly.",
      503
    );
  }
  return json({ url: target, filename: "PitWall-Setup.exe" });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true });
    }
    if (request.method === "POST" && url.pathname === "/activate") {
      try {
        return await handleActivate(request, env);
      } catch (e) {
        return err("server_error", "Activation failed. Try again.", 500);
      }
    }
    if (request.method === "POST" && url.pathname === "/download") {
      try {
        return await handleDownload(request, env);
      } catch (e) {
        return err("server_error", "Could not check that code. Try again.", 500);
      }
    }
    return err("not_found", "Not found.", 404);
  },
};
