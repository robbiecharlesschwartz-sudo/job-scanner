// Tests for api/status.mjs with GitHub mocked out: node --test tests/status.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import handler, { authorized, fromSite, sameOrigin, validate } from "../api/status.mjs";

let file;      // the fake state/status.json in the repo
let writes;

beforeEach(() => {
  file = { "Acme|1": { status: "applied", at: "2026-10-01" } };
  writes = 0;
  globalThis.fetch = async (url, opts = {}) => {
    if (!opts.method || opts.method === "GET") {
      return { ok: true, status: 200, json: async () => ({
        content: Buffer.from(JSON.stringify(file)).toString("base64"), sha: "s1" }) };
    }
    writes++;
    file = JSON.parse(Buffer.from(JSON.parse(opts.body).content, "base64").toString());
    return { ok: true, status: 200 };
  };
  delete process.env.STATUS_TOKEN;
});

function call(method, { body, headers = {} } = {}) {
  const res = { code: 0, body: undefined, headers: {},
    setHeader(k, v) { this.headers[k] = v; },
    status(c) { this.code = c; return this; },
    json(b) { this.body = b; return this; },
    end() { return this; } };
  return handler({ method, body, headers: { host: "site.example", ...headers } }, res).then(() => res);
}

test("validate accepts real keys and rejects junk", () => {
  assert.deepEqual(validate({ key: "Deloitte|364895", status: "applied" }), { key: "Deloitte|364895", status: "applied" });
  assert.ok(validate({ key: "Acme|1", status: null }));
  for (const b of [{ key: "nopipe", status: "applied" }, { key: "A|1", status: "hired" },
                   { key: "A|1\nB|2", status: "applied" }, { key: "x".repeat(400) + "|1", status: null }, null]) {
    assert.equal(validate(b), null);
  }
});

test("the site's own pages are allowed on any device; others need the exact token", () => {
  const site = { host: "site.example", "sec-fetch-site": "same-origin" };
  assert.equal(authorized({}, undefined), true);
  assert.equal(authorized(site, "abc"), true);                                   // any device, no setup
  assert.equal(authorized({ host: "site.example", origin: "https://site.example" }, "abc"), true);
  assert.equal(authorized({ host: "site.example", referer: "https://site.example/" }, "abc"), true);
  assert.equal(authorized({ "x-status-token": "abc" }, "abc"), true);
  assert.equal(authorized({ "x-status-token": "abd" }, "abc"), false);
  assert.equal(authorized({ host: "site.example" }, "abc"), false);              // e.g. a bare script
  assert.equal(authorized({ host: "site.example", origin: "https://evil.example",
                            "sec-fetch-site": "cross-site" }, "abc"), false);
  assert.equal(fromSite({ host: "a.vercel.app", "x-forwarded-host": "site.example",
                          origin: "https://site.example" }), true);
});

test("cross-site POSTs are refused", () => {
  assert.equal(sameOrigin({ host: "site.example" }), true);
  assert.equal(sameOrigin({ host: "site.example", origin: "https://site.example" }), true);
  assert.equal(sameOrigin({ host: "site.example", origin: "https://evil.example" }), false);
});

test("mark, then undo, persists to the repo file", async () => {
  let r = await call("POST", { body: { key: "Acme|2", status: "not_interested" } });
  assert.equal(r.code, 200);
  assert.equal(file["Acme|2"].status, "not_interested");
  r = await call("POST", { body: JSON.stringify({ key: "Acme|2", status: null }) });
  assert.equal(r.code, 200);
  assert.equal(file["Acme|2"], undefined);
  assert.equal(file["Acme|1"].status, "applied");        // other marks untouched
  assert.equal(writes, 2);
});

test("with STATUS_TOKEN set, the page still syncs on a new device with no token", async () => {
  process.env.STATUS_TOKEN = "s3cret";
  const page = { "sec-fetch-site": "same-origin", referer: "https://site.example/" };
  let r = await call("GET", { headers: page });
  assert.equal(r.code, 200);
  assert.equal(r.body["Acme|1"].status, "applied");
  r = await call("POST", { body: { key: "Acme|5", status: "applied" }, headers: { ...page, origin: "https://site.example" } });
  assert.equal(r.code, 200);
  assert.equal(file["Acme|5"].status, "applied");
});

test("with STATUS_TOKEN set, non-page requests without it are rejected and nothing is written", async () => {
  process.env.STATUS_TOKEN = "s3cret";
  let r = await call("GET");
  assert.equal(r.code, 401);
  r = await call("POST", { body: { key: "Acme|3", status: "applied" } });
  assert.equal(r.code, 401);
  assert.equal(writes, 0);
  r = await call("POST", { body: { key: "Acme|3", status: "applied" }, headers: { "x-status-token": "s3cret" } });
  assert.equal(r.code, 200);
  assert.equal(file["Acme|3"].status, "applied");
});

test("bad input and cross-site requests never reach GitHub", async () => {
  let r = await call("POST", { body: "{not json" });
  assert.equal(r.code, 400);
  r = await call("POST", { body: { key: "Acme|4", status: "applied" }, headers: { origin: "https://evil.example" } });
  assert.equal(r.code, 403);
  r = await call("DELETE");
  assert.equal(r.code, 405);
  assert.equal(writes, 0);
});
