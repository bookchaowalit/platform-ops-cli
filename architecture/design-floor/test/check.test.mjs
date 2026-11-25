// Fixture and unit tests for check.mjs. Needs a Playwright Chromium:
//   npm ci && npx playwright install --only-shell chromium && npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { writeFile, mkdtemp, copyFile, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { loadConfig, runChecks, applyWaivers, axeRule, startTarget } from "../check.mjs";
import { FIXTURES, build } from "./build-fixtures.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

await build();

for (const [name, [, expected]] of Object.entries(FIXTURES)) {
  test(`fixture ${name} ${expected ? `fails only ${expected}` : "passes"}`, async () => {
    const cfg = await loadConfig(path.join(ROOT, "fixtures", name, "design-floor.json"));
    const rules = [...new Set((await runChecks(cfg)).map((f) => f.rule))].sort();
    assert.deepEqual(rules, expected ? [expected] : []);
  });
}

/** @param {object} obj */
async function tempConfig(obj) {
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-"));
  const file = path.join(dir, "design-floor.json");
  await writeFile(file, JSON.stringify(obj));
  return file;
}

const base = { target: { url: "http://127.0.0.1:1" }, routes: ["/"] };

/**
 * A finding as runChecks reports it; only rule, route and width matter to the waiver tests.
 * @param {string} rule
 * @param {string} route
 * @param {number} [width]
 * @returns {import("../check.mjs").Finding}
 */
const finding = (rule, route, width = 390) => ({ rule, route, width, el: "html", detail: "" });

test("config: rejects a missing target, empty routes and missing floor widths", async () => {
  await assert.rejects(loadConfig(await tempConfig({ routes: ["/"] })), /target needs/);
  await assert.rejects(loadConfig(await tempConfig({ ...base, routes: [] })), /routes/);
  await assert.rejects(loadConfig(await tempConfig({ ...base, widths: [375, 1440] })), /widths must include 320/);
});

test("config: a route is a path or { path, status }", async () => {
  const cfg = await loadConfig(await tempConfig({ ...base, routes: ["/", { path: "/no-such-page/", status: 404 }] }));
  assert.deepEqual(cfg.routes, [{ path: "/", status: null }, { path: "/no-such-page/", status: 404 }]);
  await assert.rejects(loadConfig(await tempConfig({ ...base, routes: ["no-slash"] })), /routes\[0\]/);
  await assert.rejects(loadConfig(await tempConfig({ ...base, routes: [{ path: "/x" }] })), /routes\[0\]/);
  await assert.rejects(loadConfig(await tempConfig({ ...base, routes: [{ path: "/x", status: "404" }] })), /routes\[0\]/);
});

test("expected status: a static miss serves 404.html with 404, which passes only when the route expects it", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-404-"));
  await copyFile(path.join(ROOT, "fixtures", "good", "index.html"), path.join(dir, "404.html"));
  await copyFile(path.join(ROOT, "tokens.css"), path.join(dir, "tokens.css"));
  await symlink(path.join(ROOT, "node_modules"), path.join(dir, "node_modules"));
  const cfg = (/** @type {unknown[]} */ routes) => tempConfig({ target: { static: dir }, routes });
  assert.deepEqual(await runChecks(await loadConfig(await cfg([{ path: "/no-such-page/", status: 404 }]))), []);
  await assert.rejects(runChecks(await loadConfig(await cfg(["/no-such-page/"]))), /returned 404$/);
  await assert.rejects(runChecks(await loadConfig(await cfg([{ path: "/404.html", status: 404 }]))), /returned 200, expected 404/);
});

test("screenshots: distinct routes never share a file name", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-shots-"));
  await copyFile(path.join(ROOT, "fixtures", "good", "index.html"), path.join(dir, "index.html"));
  await copyFile(path.join(ROOT, "fixtures", "good", "index.html"), path.join(dir, "404.html"));
  await copyFile(path.join(ROOT, "tokens.css"), path.join(dir, "tokens.css"));
  await symlink(path.join(ROOT, "node_modules"), path.join(dir, "node_modules"));
  const shots = path.join(dir, "shots");
  const routes = ["/", { path: "/root", status: 404 }, { path: "/a-b", status: 404 }, { path: "/a/b", status: 404 }];
  const cfg = await loadConfig(await tempConfig({ target: { static: dir }, routes, widths: [320, 375, 390, 768, 1440] }));
  await runChecks(cfg, { shots });
  const { readdir } = await import("node:fs/promises");
  assert.equal((await readdir(shots)).length, routes.length * 5);
});

test("config: serve must be an argv array, never a shell string", async () => {
  const serve = (/** @type {unknown} */ s) => ({ target: { serve: s, url: "http://127.0.0.1:4173" }, routes: ["/"] });
  await assert.rejects(loadConfig(await tempConfig(serve("npm start; curl evil"))), /argv array/);
  await assert.rejects(loadConfig(await tempConfig(serve([]))), /argv array/);
  await loadConfig(await tempConfig(serve(["npm", "run", "start"])));
});

test("config: waivers need a known rule, a reason and an expiry date", async () => {
  await assert.rejects(loadConfig(await tempConfig({ ...base, waivers: [{ rule: "nope", reason: "x", expires: "2027-01-01" }] })), /unknown/);
  await assert.rejects(loadConfig(await tempConfig({ ...base, waivers: [{ rule: "reflow", expires: "2027-01-01" }] })), /reason/);
  await assert.rejects(loadConfig(await tempConfig({ ...base, waivers: [{ rule: "reflow", reason: "x" }] })), /expires/);
});

test("waivers: active waiver moves findings, expired and non-waivable ones do not", async () => {
  const findings = [
    finding("reflow", "/", 320),
    finding("reduced-motion", "/demo"),
    finding("contrast", "/"),
  ];
  const cfg = await loadConfig(await tempConfig({
    ...base,
    waivers: [
      { rule: "reflow", reason: "wide data table", expires: "2027-04-30" },
      { rule: "reduced-motion", reason: "motion demo", expires: "2026-01-01" },
      { rule: "contrast", reason: "brand", expires: "2027-04-30" },
    ],
  }));
  const r = applyWaivers(cfg, findings, "2026-10-29");
  assert.deepEqual(r.waived.map((f) => f.rule), ["reflow"]);
  assert.deepEqual(r.failed.map((f) => f.rule), ["reduced-motion", "contrast"]);
  assert.equal(r.problems.length, 2);
  assert.match(r.problems.join(" "), /contrast not allowed on a public app/);
  assert.match(r.problems.join(" "), /reduced-motion expired/);
});

test("waivers: contrast may be waived when the app is not public", async () => {
  const cfg = await loadConfig(await tempConfig({ ...base, public: false, waivers: [{ rule: "contrast", reason: "internal tool", expires: "2027-04-30" }] }));
  const r = applyWaivers(cfg, [finding("contrast", "/")], "2026-10-29");
  assert.equal(r.failed.length, 0);
  assert.equal(r.problems.length, 0);
});

test("waivers: a route-scoped waiver covers only its routes", async () => {
  const cfg = await loadConfig(await tempConfig({ ...base, waivers: [{ rule: "reflow", reason: "table", expires: "2027-04-30", routes: ["/data"] }] }));
  const r = applyWaivers(cfg, [finding("reflow", "/data"), finding("reflow", "/")], "2026-10-29");
  assert.deepEqual(r.waived.map((f) => f.route), ["/data"]);
  assert.deepEqual(r.failed.map((f) => f.route), ["/"]);
});

test("axe: target-size and contrast report under their own floor rows, so their waivers apply", () => {
  assert.equal(axeRule("target-size"), "target-size");
  assert.equal(axeRule("color-contrast"), "contrast");
  assert.equal(axeRule("html-has-lang"), "lang");
  assert.equal(axeRule("image-alt"), "axe");
});

test("serve: the app is stopped when Chromium fails to launch", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-serve-"));
  const port = 41000 + Math.floor(Math.random() * 2000);
  await writeFile(path.join(dir, "server.mjs"), `import { createServer } from "node:http"; createServer((q, r) => r.end("ok")).listen(${port}, "127.0.0.1");`);
  const url = `http://127.0.0.1:${port}/`;
  const cfg = await loadConfig(await tempConfig({ target: { serve: [process.execPath, path.join(dir, "server.mjs")], url, timeoutSeconds: 10 }, routes: ["/"] }));
  cfg.baseDir = dir;
  await assert.rejects(runChecks(cfg, { launch: async () => { throw new Error("no chromium"); } }), /no chromium/);
  await new Promise((r) => setTimeout(r, 500));
  await assert.rejects(fetch(url));
});

test("serve: a target that accepts but never answers still times out", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-stall-"));
  const port = 43000 + Math.floor(Math.random() * 2000);
  await writeFile(path.join(dir, "server.mjs"), `import { createServer } from "node:http"; createServer(() => {}).listen(${port}, "127.0.0.1");`);
  const cfg = await loadConfig(await tempConfig({ target: { serve: [process.execPath, path.join(dir, "server.mjs")], url: `http://127.0.0.1:${port}/`, timeoutSeconds: 2 }, routes: ["/"] }));
  cfg.baseDir = dir;
  const start = Date.now();
  await assert.rejects(runChecks(cfg), /did not answer/);
  assert.ok(Date.now() - start < 8000, `took ${Date.now() - start} ms`);
});

test("static: a path that escapes into a sibling folder sharing the root's prefix is refused", async () => {
  const { request } = await import("node:http");
  const { mkdir } = await import("node:fs/promises");
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-root-"));
  await mkdir(path.join(dir, "site"));
  await mkdir(path.join(dir, "site-secret"));
  await writeFile(path.join(dir, "site", "index.html"), "ok");
  await writeFile(path.join(dir, "site-secret", "key.txt"), "secret");
  const cfg = await loadConfig(await tempConfig({ target: { static: path.join(dir, "site") }, routes: ["/"] }));
  const target = await startTarget(cfg);
  try {
    // %2f keeps the URL parser from normalising "..", so the server sees /../site-secret/key.txt.
    const status = await new Promise((resolve, reject) => {
      request(`${target.url}/..%2fsite-secret%2fkey.txt`, (res) => { res.resume(); resolve(res.statusCode); }).on("error", reject).end();
    });
    assert.equal(status, 400);
  } finally {
    await target.stop();
  }
});

test("static: a directory without its trailing slash redirects, as static hosts do", async () => {
  const { request } = await import("node:http");
  const cfg = await loadConfig(path.join(ROOT, "fixtures", "good", "design-floor.json"));
  const target = await startTarget(cfg);
  try {
    const { status, location } = await new Promise((resolve, reject) => {
      request(`${target.url}/fixtures/good?x=1`, (res) => { res.resume(); resolve({ status: res.statusCode, location: res.headers.location }); })
        .on("error", reject).end();
    });
    assert.equal(status, 301);
    assert.equal(location, "/fixtures/good/?x=1");
  } finally {
    await target.stop();
  }
});

test("static: a directory redirect keeps the request's escaping", async () => {
  const { request } = await import("node:http");
  const { mkdir } = await import("node:fs/promises");
  const dir = await mkdtemp(path.join(tmpdir(), "design-floor-escape-"));
  await mkdir(path.join(dir, "a#b"));
  await writeFile(path.join(dir, "a#b", "index.html"), "ok");
  const cfg = await loadConfig(await tempConfig({ target: { static: dir }, routes: ["/"] }));
  const target = await startTarget(cfg);
  try {
    const location = await new Promise((resolve, reject) => {
      request(`${target.url}/a%23b`, (res) => { res.resume(); resolve(res.headers.location); }).on("error", reject).end();
    });
    assert.equal(location, "/a%23b/");
  } finally {
    await target.stop();
  }
});

test("serve: a port that already answers before the app starts is refused", async () => {
  const { createServer } = await import("node:http");
  const other = createServer((q, r) => r.end("someone else")).listen(0, "127.0.0.1");
  await new Promise((r) => other.once("listening", r));
  const port = /** @type {import("node:net").AddressInfo} */ (other.address()).port;
  try {
    const cfg = await loadConfig(await tempConfig({ target: { serve: [process.execPath, "-e", "setTimeout(() => {}, 5000)"], url: `http://127.0.0.1:${port}/`, timeoutSeconds: 5 }, routes: ["/"] }));
    await assert.rejects(startTarget(cfg), /already answers/);
  } finally {
    other.close();
  }
});

test("serve: an app that exits during startup fails at once, not at the deadline", async () => {
  const port = 45000 + Math.floor(Math.random() * 2000);
  const cfg = await loadConfig(await tempConfig({ target: { serve: [process.execPath, "-e", "process.exit(3)"], url: `http://127.0.0.1:${port}/`, timeoutSeconds: 30 }, routes: ["/"] }));
  const start = Date.now();
  await assert.rejects(startTarget(cfg), /exited with code 3/);
  assert.ok(Date.now() - start < 5000, `took ${Date.now() - start} ms`);
});
