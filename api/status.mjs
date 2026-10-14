// Saves "Applied" / "Not interested" check-offs from the results page into state/status.json in
// the private repo, so they sync across devices and the scanner can read them.
// Env: GITHUB_TOKEN (repo contents write), STATUS_KEY (shared secret the page sends), REPO.
const REPO = process.env.REPO || "robbiecharlesschwartz-sudo/job-scanner";
const PATH = "state/status.json";
const ALLOWED = new Set(["applied", "not_interested"]);

async function gh(method, body) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/contents/${PATH}`, {
    method,
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "job-scanner-status",
    },
    body: body && JSON.stringify(body),
  });
  return r;
}

async function load() {
  const r = await gh("GET");
  if (r.status === 404) return { data: {}, sha: undefined };
  if (!r.ok) throw new Error(`GitHub read ${r.status}`);
  const j = await r.json();
  return { data: JSON.parse(Buffer.from(j.content, "base64").toString("utf8") || "{}"), sha: j.sha };
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (!process.env.STATUS_KEY || req.headers["x-status-key"] !== process.env.STATUS_KEY) {
    return res.status(401).json({ error: "bad key" });
  }
  try {
    if (req.method === "GET") return res.status(200).json((await load()).data);
    if (req.method !== "POST") return res.status(405).end();

    const { key, status } = typeof req.body === "string" ? JSON.parse(req.body) : req.body || {};
    if (typeof key !== "string" || key.length > 300 || (status !== null && !ALLOWED.has(status))) {
      return res.status(400).json({ error: "bad request" });
    }
    for (let attempt = 0; attempt < 3; attempt++) {   // retry if the file changed underneath us
      const { data, sha } = await load();
      if (status === null) delete data[key];
      else data[key] = { status, at: new Date().toISOString().slice(0, 10) };
      const r = await gh("PUT", {
        message: `Mark ${status || "undo"}: ${key}`.slice(0, 120),
        content: Buffer.from(JSON.stringify(data, null, 1) + "\n").toString("base64"),
        sha,
        committer: { name: "Job Scanner", email: "robbiecharlesschwartz@gmail.com" },
      });
      if (r.ok) return res.status(200).json(data);
      if (r.status !== 409 && r.status !== 422) throw new Error(`GitHub write ${r.status}`);
    }
    return res.status(409).json({ error: "busy, try again" });
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
}
