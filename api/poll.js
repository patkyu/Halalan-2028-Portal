// Visitor poll for the practice ballot. Not a survey: only people who visit the site and choose to vote.
//
// Runs as a Vercel serverless function at /api/poll. Stores tallies in Upstash Redis
// (Vercel Marketplace → Upstash Redis, which sets KV_REST_API_URL and KV_REST_API_TOKEN,
// or set UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN). Set POLL_SALT to any long random string.
//
// It accepts ballots only when data.json has ballot.official = true and poll.open = true,
// and only for the candidate numbers in data.json's official list. No names, emails or raw IPs are stored:
// one random device cookie plus an hourly per-network limit (salted hash of the IP).
const crypto = require("crypto");
const DATA = require("../data.json");

const URL_ = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const TOKEN = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
const SALT = process.env.POLL_SALT || "";
const COOKIE = "h28poll";
const PER_NETWORK_PER_HOUR = 40; // generous: many Filipino mobile users share one IP (carrier NAT)

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
  const b = DATA.ballot || {}, p = DATA.poll || {};
  if (!b.official || !(b.positions || []).length || !p.open) return false;
  if (p.closes && now > new Date(`${p.closes}T23:59:59+08:00`)) return false;
  return true;
}

function cookies(req) {
  return Object.fromEntries((req.headers.cookie || "").split(";").map(c => c.trim().split("=")).filter(c => c[0]).map(([k, ...v]) => [k, decodeURIComponent(v.join("="))]));
}

// Turns {job: [numbers]} into the votes to count. Over-votes and unknown numbers make that position invalid.
function tally(votes) {
  const counted = {}, over = [];
  for (const pos of DATA.ballot.positions) {
    const picks = votes && votes[pos.job];
    if (!Array.isArray(picks) || !picks.length) continue;
    const valid = new Set(pos.candidates.map(c => c.no));
    const uniq = [...new Set(picks)];
    if (uniq.length !== picks.length || uniq.some(n => !Number.isInteger(n) || !valid.has(n))) return { error: `bad choice for ${pos.job}` };
    if (uniq.length > pos.max) { over.push(pos.job); continue; }
    counted[pos.job] = uniq;
  }
  return { counted, over };
}

async function results() {
  const jobs = DATA.ballot.positions.map(p => p.job);
  const res = await redis([["GET", "poll:ballots"], ["HGETALL", "poll:valid"], ...jobs.map(j => ["HGETALL", `poll:r:${j}`])]);
  const pairs = a => { const o = {}; for (let i = 0; i < (a || []).length; i += 2) o[a[i]] = +a[i + 1]; return o; };
  return { ballots: +(res[0] || 0), valid: pairs(res[1]), results: Object.fromEntries(jobs.map((j, i) => [j, pairs(res[i + 2])])) };
}

module.exports = async function handler(req, res) {
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
    if (voted) return send(409, { error: "You already sent a ballot from this device." });

    const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
    const t = tally(body.votes);
    if (t.error) return send(400, { error: t.error });
    if (!Object.keys(t.counted).length && !t.over.length) return send(400, { error: "Empty ballot" });

    const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim();
    const net = crypto.createHash("sha256").update(SALT + ip).digest("hex").slice(0, 24);
    const hour = Math.floor(Date.now() / 3600e3);
    const [n] = await redis([["INCR", `poll:net:${net}:${hour}`], ["EXPIRE", `poll:net:${net}:${hour}`, 3600]]);
    if (n > PER_NETWORK_PER_HOUR) return send(429, { error: "Too many ballots from this network. Try again later." });

    const cmds = [["INCR", "poll:ballots"]];
    for (const [job, nums] of Object.entries(t.counted)) {
      cmds.push(["HINCRBY", "poll:valid", job, 1]);
      for (const no of nums) cmds.push(["HINCRBY", `poll:r:${job}`, String(no), 1]);
    }
    for (const job of t.over) cmds.push(["HINCRBY", "poll:over", job, 1]);
    await redis(cmds);

    const id = crypto.randomBytes(12).toString("hex");
    const closes = DATA.poll.closes ? new Date(`${DATA.poll.closes}T23:59:59+08:00`) : new Date(Date.now() + 400 * 864e5);
    res.setHeader("Set-Cookie", `${COOKIE}=${id}; Path=/; Expires=${closes.toUTCString()}; HttpOnly; Secure; SameSite=Lax`);
    return send(200, { ok: true, over: t.over, ...(await results()), voted: true });
  } catch (e) {
    return send(500, { error: "Poll is unavailable right now." });
  }
};
module.exports.tally = tally;
module.exports.isOpen = isOpen;
