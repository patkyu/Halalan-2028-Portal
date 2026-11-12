// Visitor write-in poll for President and Vice President. Not a survey: only people who visit
// the site and choose to vote.
//
// Runs as a Vercel serverless function at /api/poll. Stores tallies in Upstash Redis
// (Vercel Marketplace → Upstash Redis sets KV_REST_API_URL and KV_REST_API_TOKEN;
// UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN also work). Set POLL_SALT to a long random string.
//
// Typed names are matched to known people with namematch.js, so "Sarah Dturete" counts as Sara Duterte.
// Names it cannot match are counted under "Iba pa"; the raw text is kept (not shown publicly) in
// poll:other:<pos> so a person can add aliases or new names in data.json later.
// No names of voters, emails or raw IPs are stored: one random device cookie plus an hourly
// per-network limit (salted hash of the IP).
const crypto = require("crypto");
const DATA = require("../data.json");
const NM = require("../namematch.js");

const URL_ = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const TOKEN = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
const SALT = process.env.POLL_SALT || "";
const COOKIE = "h28poll";
const PER_NETWORK_PER_HOUR = 40; // generous: many Filipino mobile users share one IP (carrier NAT)
const POLL = DATA.poll || {};
const POSITIONS = Object.keys(POLL.positions || {});

function pool() {
  const people = Object.entries(DATA.people).map(([id, p]) => ({ id, name: p.name, aliases: (POLL.aliases || {})[id] }));
  return people.concat((POLL.extra || []).map(e => ({ id: e.id, name: e.name, aliases: e.aliases })));
}
const POOL = pool(), INDEX = NM.build(POOL), IDS = new Set(POOL.map(p => p.id));

async function redis(cmds) {
  const r = await fetch(`${URL_}/pipeline`, {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify(cmds),
  });
  if (!r.ok) throw new Error(`redis ${r.status}`);
  const out = await r.json();
  return out.map(x => { if (x.error) throw new Error(x.error); return x.result; });
}

function isOpen(now = new Date()) {
  if (!POLL.open || !POSITIONS.length) return false;
  if (POLL.closes && now > new Date(`${POLL.closes}T23:59:59+08:00`)) return false;
  return true;
}

function cookies(req) {
  return Object.fromEntries((req.headers.cookie || "").split(";").map(c => c.trim().split("=")).filter(c => c[0]).map(([k, ...v]) => [k, decodeURIComponent(v.join("="))]));
}

// body: {pres: {text, id?}, vp: {text, id?}}. `id` is set when the voter picked a suggested name.
// Returns {counted: {pos: personId | "other"}, others: {pos: normalizedText}} or {error}.
function resolve(body) {
  const counted = {}, others = {};
  for (const pos of POSITIONS) {
    const v = body && body[pos];
    if (!v || (!v.text && !v.id)) continue;
    const text = String(v.text || "").slice(0, 80);
    if (v.id) {
      if (v.id === "other") { counted[pos] = "other"; }
      else if (IDS.has(v.id)) { counted[pos] = v.id; continue; }
      else return { error: `unknown name for ${pos}` };
    } else {
      const m = NM.match(text, INDEX);
      if (m.status === "match") { counted[pos] = m.id; continue; }
      counted[pos] = "other";
    }
    const n = NM.norm(text).slice(0, 60);
    if (n) others[pos] = n;
  }
  return { counted, others };
}

async function results() {
  const res = await redis([["GET", "poll:ballots"], ...POSITIONS.map(p => ["HGETALL", `poll:r:${p}`])]);
  const pairs = a => { const o = {}; for (let i = 0; i < (a || []).length; i += 2) o[a[i]] = +a[i + 1]; return o; };
  return { ballots: +(res[0] || 0), results: Object.fromEntries(POSITIONS.map((p, i) => [p, pairs(res[i + 1])])) };
}

async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const send = (code, body) => res.status(code).json(body);
  if (!isOpen()) return send(403, { open: false, error: "The visitor poll is not open." });
  if (!URL_ || !TOKEN) return send(503, { open: true, error: "Poll storage is not configured." });

  const voted = !!cookies(req)[COOKIE];
  try {
    if (req.method === "GET") {
      const r = await results();
      // Tallies are shown only to people who already voted, so they do not sway anyone's choice.
      return send(200, voted ? { open: true, voted, ...r } : { open: true, voted, ballots: r.ballots });
    }
    if (req.method !== "POST") { res.setHeader("Allow", "GET, POST"); return send(405, { error: "Method not allowed" }); }
    if (voted) return send(409, { error: "You already voted from this device." });

    const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
    const t = resolve(body);
    if (t.error) return send(400, { error: t.error });
    if (!Object.keys(t.counted).length) return send(400, { error: "Empty ballot" });

    const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim();
    const net = crypto.createHash("sha256").update(SALT + ip).digest("hex").slice(0, 24);
    const hour = Math.floor(Date.now() / 3600e3);
    const [n] = await redis([["INCR", `poll:net:${net}:${hour}`], ["EXPIRE", `poll:net:${net}:${hour}`, 3600]]);
    if (n > PER_NETWORK_PER_HOUR) return send(429, { error: "Too many votes from this network. Try again later." });

    const cmds = [["INCR", "poll:ballots"]];
    for (const [pos, id] of Object.entries(t.counted)) cmds.push(["HINCRBY", `poll:r:${pos}`, id, 1]);
    for (const [pos, text] of Object.entries(t.others)) cmds.push(["HINCRBY", `poll:other:${pos}`, text, 1]);
    await redis(cmds);

    const id = crypto.randomBytes(12).toString("hex");
    const until = POLL.closes ? new Date(`${POLL.closes}T23:59:59+08:00`) : new Date(Date.now() + 400 * 864e5);
    res.setHeader("Set-Cookie", `${COOKIE}=${id}; Path=/; Expires=${until.toUTCString()}; HttpOnly; Secure; SameSite=Lax`);
    return send(200, { ok: true, counted: t.counted, ...(await results()), voted: true });
  } catch (e) {
    return send(500, { error: "Poll is unavailable right now." });
  }
}
module.exports = handler;
module.exports.resolve = resolve;
module.exports.isOpen = isOpen;
