// Saves "Applied" / "Not interested" check-offs from the results page into state/status.json in
// the repo, so they sync across devices and the scanner can read them.
//
// Env (Vercel project settings):
//   GITHUB_TOKEN   fine-grained token with Contents: read & write on this repo only (required)
//   STATUS_TOKEN   optional shared secret for requests that do NOT come from the results page
//                  itself (scripts, other sites). The page works on every device with no setup:
//                  browsers mark its own requests as same-site, and those are accepted.
//   REPO           owner/name, defaults to this repo
import { timingSafeEqual } from "node:crypto";

const REPO = process.env.REPO || "robbiecharlesschwartz-sudo/job-scanner";
const PATH = "state/status.json";
const ALLOWED = new Set(["applied", "not_interested"]);
const KEY_RE = /^[^|\n\r]{1,100}\|[^\n\r]{1,200}$/;   // "<company>|<board job id>"
const MAX_ENTRIES = 5000;                               // stops a flood of junk keys bloating the repo

async function gh(method, body) {
  return fetch(`https://api.github.com/repos/${REPO}/contents/${PATH}`, {
    method,
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "job-scanner-status",
    },
    body: body && JSON.stringify(body),
  });
}

async function load() {
  const r = await gh("GET");
  if (r.status === 404) return { data: {}, sha: undefined };
  if (!r.ok) throw new Error(`GitHub read ${r.status}`);
  const j = await r.json();
  let data = {};
  try {
    data = JSON.parse(Buffer.from(j.content, "base64").toString("utf8") || "{}");
  } catch {
    data = {};                                          // corrupt file: start over rather than fail forever
  }
  return { data: data && typeof data === "object" && !Array.isArray(data) ? data : {}, sha: j.sha };
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return null; }
}

// True when the browser says the request comes from the results page itself.
export function fromSite(headers) {
  const hosts = new Set([headers.host, headers["x-forwarded-host"]].filter(Boolean));
  if (headers["sec-fetch-site"] === "same-origin") return true;
  if (headers.origin) return hosts.has(hostOf(headers.origin));
  if (headers.referer) return hosts.has(hostOf(headers.referer));
  return false;
}

export function tokenOk(headers, token = process.env.STATUS_TOKEN) {
  if (!token) return false;
  const got = Buffer.from(String(headers["x-status-token"] || ""));
  const want = Buffer.from(token);
  return got.length === want.length && timingSafeEqual(got, want);
}

// The page itself is always allowed (so marks sync on every device with no setup); anything else
// needs the token when one is configured. With no STATUS_TOKEN set, behaviour is fully open.
export function authorized(headers, token = process.env.STATUS_TOKEN) {
  return !token || fromSite(headers) || tokenOk(headers, token);
}

export function sameOrigin(headers) {
  // Browsers send Origin on cross-site POSTs; refuse those (defence in depth against CSRF).
  const origin = headers.origin;
  if (!origin) return true;
  const hosts = new Set([headers.host, headers["x-forwarded-host"]].filter(Boolean));
  return hosts.has(hostOf(origin));
}

export function validate(body) {
  const { key, status } = body || {};
  if (typeof key !== "string" || !KEY_RE.test(key)) return null;
  if (status !== null && !ALLOWED.has(status)) return null;
  return { key, status };
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  try {
    if (!authorized(req.headers)) return res.status(401).json({ error: "unauthorized" });
    if (req.method === "GET") return res.status(200).json((await load()).data);
    if (req.method !== "POST") return res.status(405).end();
    if (!sameOrigin(req.headers)) return res.status(403).json({ error: "cross-site request refused" });

    let body = req.body;
    if (typeof body === "string") {
      try { body = JSON.parse(body); } catch { body = null; }
    }
    const v = validate(body);
    if (!v) return res.status(400).json({ error: "bad request" });

    for (let attempt = 0; attempt < 3; attempt++) {   // retry if the file changed underneath us
      const { data, sha } = await load();
      if (v.status === null) delete data[v.key];
      else {
        if (!(v.key in data) && Object.keys(data).length >= MAX_ENTRIES) {
          return res.status(507).json({ error: "too many marks stored" });
        }
        data[v.key] = { status: v.status, at: new Date().toISOString().slice(0, 10) };
      }
      const r = await gh("PUT", {
        message: `Mark ${v.status || "undo"}: ${v.key}`.slice(0, 120),
        content: Buffer.from(JSON.stringify(data, null, 1) + "\n").toString("base64"),
        sha,
        committer: { name: "Job Scanner", email: "robbiecharlesschwartz@gmail.com" },
      });
      if (r.ok) return res.status(200).json(data);
      if (r.status !== 409 && r.status !== 422) throw new Error(`GitHub write ${r.status}`);
    }
    return res.status(409).json({ error: "busy, try again" });
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e).slice(0, 100) });
  }
}
