#!/usr/bin/env node
// ADR-0007 design floor check. Runs the automated and keyboard-script rows of
// FLOOR.md against one app, configured by its design-floor.json.
//
//   node check.mjs --config path/to/design-floor.json [--json report.json]
//                  [--shots dir] [--today YYYY-MM-DD]
//
// Exit 0: no unwaived failures. Exit 1: failures. Exit 2: config or run error.
// A pass is not a WCAG 2.2 AA conformance claim (see FLOOR.md).

import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { readFile, stat, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";

export const FLOOR_VERSION = "1.0.0";
export const DEFAULT_WIDTHS = [320, 375, 390, 768, 1440];
export const RULES = [
  "contrast", "axe", "thai-font", "thai-typography", "lang", "reflow",
  "target-size", "skip-link", "focus-visible", "focus-not-obscured",
  "keyboard-trap", "reduced-motion",
];
export const NOT_WAIVABLE_ON_PUBLIC = new Set([
  "contrast", "skip-link", "focus-visible", "focus-not-obscured", "keyboard-trap",
]);
const MANUAL_ROWS = ["contrast over images/gradients", "meaningful names", "lang of Latin passages", "states", "keyboard-operable"];
const AXE_LANG_RULES = new Set(["html-has-lang", "html-lang-valid", "html-xml-lang-mismatch", "valid-lang"]);
const AXE_TAGS =["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const HEIGHT = 900;
const MAX_TAB_STOPS = 40;
const MOTION_LIMIT_MS = 200;
const MAX_SCROLL_STEPS = 60; // an infinite feed keeps growing; stop revealing after this many screens

/** @typedef {{ path: string, status: number | null }} Route */
/** @typedef {{ rule: string, reason: string, expires: string, routes?: string[] }} Waiver */
/** @typedef {{ static?: string, serve?: string[], url?: string, timeoutSeconds?: number }} Target */
/** @typedef {{ floor: string, public: boolean, target: Target, routes: Route[], widths: number[], waivers: Waiver[], baseDir: string }} Config */
/** @typedef {{ rule: string, route: string, width: number, el: string, detail: string }} Finding */
/** @typedef {{ failed: Finding[], waived: Finding[], problems: string[] }} Result */
/** @typedef {{ url: string, stop: () => Promise<void> }} Running */
/** @typedef {{ el: string, detail: string }} Issue */
/** @typedef {import("playwright").Page} Page */

/** @type {Record<string, string>} */
const MIME = {
  ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
  ".mjs": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml",
  ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
  ".woff2": "font/woff2", ".woff": "font/woff", ".ico": "image/x-icon", ".txt": "text/plain",
};

// ---------- config ----------

/**
 * @param {string} file
 * @returns {Promise<Config>}
 */
export async function loadConfig(file) {
  const raw = JSON.parse(await readFile(file, "utf8"));
  const errors = [];
  const t = raw.target ?? {};
  const kinds = ["static", "serve", "url"].filter((k) => k in t);
  if (kinds.length === 0) errors.push("target needs one of: static, serve (+url), url");
  if (t.static && t.serve) errors.push("target.static and target.serve are exclusive");
  if (t.serve && !t.url) errors.push("target.serve needs target.url");
  // argv array, run without a shell, so the config cannot smuggle shell syntax.
  if (t.serve && !(Array.isArray(t.serve) && t.serve.length > 0 && t.serve.every((/** @type {unknown} */ a) => typeof a === "string" && a))) {
    errors.push('target.serve must be a non-empty argv array, e.g. ["npm", "run", "start"]');
  }
  if (!Array.isArray(raw.routes) || raw.routes.length === 0) errors.push("routes must be a non-empty array");
  // A route is a path, or { path, status } for a page that must answer with
  // that status, such as the not-found page: { "path": "/no-such-page/", "status": 404 }.
  for (const [i, r] of (Array.isArray(raw.routes) ? raw.routes : []).entries()) {
    const ok = typeof r === "string" ? r.startsWith("/")
      : typeof r?.path === "string" && r.path.startsWith("/") && Number.isInteger(r.status) && r.status >= 200 && r.status < 600;
    if (!ok) errors.push(`routes[${i}] must be a path starting with / or { "path": "/x", "status": 404 }`);
  }
  const widths = raw.widths ?? DEFAULT_WIDTHS;
  for (const w of DEFAULT_WIDTHS) if (!widths.includes(w)) errors.push(`widths must include ${w}`);
  for (const [i, w] of (raw.waivers ?? []).entries()) {
    if (!RULES.includes(w.rule)) errors.push(`waivers[${i}].rule unknown: ${w.rule}`);
    if (!w.reason) errors.push(`waivers[${i}].reason is required`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(w.expires ?? "")) errors.push(`waivers[${i}].expires must be YYYY-MM-DD`);
  }
  if (errors.length) throw new Error(`${file}: ${errors.join("; ")}`);
  return {
    floor: raw.floor ?? FLOOR_VERSION,
    public: raw.public ?? true,
    target: t,
    routes: raw.routes.map((/** @type {string | Route} */ r) => (typeof r === "string" ? { path: r, status: null } : { path: r.path, status: r.status })),
    widths,
    waivers: raw.waivers ?? [],
    baseDir: path.dirname(path.resolve(file)),
  };
}

// ---------- target ----------

/**
 * @param {string} root
 * @returns {Promise<Running>}
 */
function staticServer(root) {
  const server = createServer(async (req, res) => {
    try {
      let p = decodeURIComponent(new URL(req.url ?? "/", "http://x").pathname);
      let file = path.join(root, p);
      // A path-segment boundary: "/site-secret" must not pass as inside "/site".
      const inside = path.relative(root, file);
      if (inside.startsWith("..") || path.isAbsolute(inside)) throw new Error("outside root");
      let s = await stat(file).catch(() => null);
      if (s?.isDirectory()) {
        // Like static hosts: /dir answers 301 to /dir/, so relative URLs in its index resolve there.
        if (!p.endsWith("/")) {
          // The request's own (still escaped) path: re-encoding the decoded one would turn a "%23"
          // in a folder name into a literal "#".
          const { pathname, search } = new URL(req.url ?? "/", "http://x");
          res.writeHead(301, { location: pathname + "/" + search }).end();
          return;
        }
        file = path.join(file, "index.html");
        s = await stat(file).catch(() => null);
      }
      if (!s && !path.extname(file)) { file += ".html"; s = await stat(file).catch(() => null); }
      if (!s) {
        // Like static hosts (Vercel, Netlify, Pages): a miss gets 404.html with status 404.
        const notFound = await readFile(path.join(root, "404.html")).catch(() => null);
        if (notFound) res.writeHead(404, { "content-type": MIME[".html"] }).end(notFound);
        else res.writeHead(404).end("not found");
        return;
      }
      res.writeHead(200, { "content-type": MIME[path.extname(file)] ?? "application/octet-stream" });
      res.end(await readFile(file));
    } catch {
      res.writeHead(400).end("bad request");
    }
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({
      url: `http://127.0.0.1:${/** @type {import("node:net").AddressInfo} */ (server.address()).port}`,
      stop: () => new Promise((r) => server.close(() => r(undefined))),
    }));
  });
}

/**
 * @param {string} url
 * @param {number} ms
 * @param {() => number | null} [exitCode] the served app's exit code once it has exited
 */
async function waitFor(url, ms, exitCode) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    // An app that exited is not up, even if something else answers on its port.
    const code = exitCode?.();
    if (code !== undefined && code !== null) throw new Error(`target exited with code ${code} before answering at ${url}`);
    // Each request gets only the time left, so an app that accepts but never answers cannot outlast the deadline.
    try { const r = await fetch(url, { signal: AbortSignal.timeout(Math.max(1, end - Date.now())) }); if (r.status < 500) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`target did not answer at ${url} within ${ms / 1000}s`);
}

/**
 * @param {Config} cfg
 * @returns {Promise<Running>}
 */
export async function startTarget(cfg) {
  const t = cfg.target;
  if (t.static) return staticServer(path.resolve(cfg.baseDir, t.static));
  // loadConfig guarantees a url with serve, and url when neither static nor serve is set.
  const url = /** @type {string} */ (t.url);
  if (t.serve) {
    // Something already answering here would be checked instead of the app (which then fails to bind).
    const taken = await fetch(url, { signal: AbortSignal.timeout(1000) }).then(() => true, () => false);
    if (taken) throw new Error(`${url} already answers before the app starts: stop whatever holds the port`);
    const [cmd, ...cmdArgs] = t.serve;
    const child = spawn(cmd, cmdArgs, { cwd: cfg.baseDir, shell: false, stdio: "ignore", detached: true });
    // The process group to signal; spawn leaves pid unset only when the command failed to start.
    const group = -(child.pid ?? 0);
    /** @type {number | null} */
    let exited = null;
    child.on("exit", (code, signal) => { exited = code ?? (signal ? 128 : 1); });
    try {
      await waitFor(url, (t.timeoutSeconds ?? 120) * 1000, () => exited);
    } catch (e) {
      try { if (group) process.kill(group, "SIGTERM"); } catch { /* already exited */ }
      throw e;
    }
    return { url, stop: async () => { try { if (group) process.kill(group, "SIGTERM"); } catch { /* gone */ } } };
  }
  return { url, stop: async () => {} };
}

// ---------- in-page probes (run in the browser) ----------

// Probes are serialised into the page, so their patterns are literals inside them.
function probeTypography() {
  const thai = /[\u0E00-\u0E7F]/;
  // Which font draws the Thai is decided from what Chromium rendered (renderedThaiFonts), not
  // from CSS family names: next/font/local and similar tools load Noto under an alias.
  /** @type {{ thaiElements: number, langMissing: boolean, typo: Issue[], lang: Issue[] }} */
  const out = { thaiElements: 0, langMissing: !document.documentElement.getAttribute("lang"), typo: [], lang: [] };
  const describe = (/** @type {Element} */ el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : "");
  // <body> itself can hold Thai text directly, so it is checked with its descendants.
  for (const el of [document.body, ...document.body.querySelectorAll("*")]) {
    // A form control's text is what it shows: a button's label, or a text field's or textarea's value, or its placeholder while empty.
    const own = el instanceof HTMLTextAreaElement ? el.value || el.placeholder : el instanceof HTMLInputElement ? (/^(submit|reset|button)$/.test(el.type) ? el.value
        : /^(text|search|url|tel|email|number)$/.test(el.type) ? el.value || el.placeholder : "")
      : [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("");
    if (!thai.test(own)) continue;
    // A shown placeholder has its own ::placeholder styles (letter-spacing included).
    const cs = getComputedStyle(el, (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && !el.value ? "::placeholder" : null);
    // The element's own visibility is authoritative (it inherits, and a child may set it back to
    // visible). checkVisibility() covers a display:none ancestor; display:contents elements have no
    // box of their own, so ask the nearest ancestor that has one.
    /** @type {Element | null} */
    let box = el;
    while (box && getComputedStyle(box).display === "contents") box = box.parentElement;
    if (cs.visibility !== "visible" || !(box ?? document.body).checkVisibility()) continue;
    if (out.thaiElements < 30) el.setAttribute("data-design-floor-thai", describe(el));
    out.thaiElements++;
    // Leading matters where Thai lines stack: boxes whose text wraps to two or
    // more lines. An inline box sits in a line box at least as tall as its
    // block's line-height and every enclosing inline box's, so it counts the tallest.
    const size = parseFloat(cs.fontSize);
    const lhPxOf = (/** @type {CSSStyleDeclaration} */ c) => (c.lineHeight === "normal" ? 1.2 * parseFloat(c.fontSize) : parseFloat(c.lineHeight));
    let lhPx = lhPxOf(cs);
    if (cs.display === "inline") {
      let up = el.parentElement;
      for (let d; up && ["inline", "contents"].includes((d = getComputedStyle(up)).display); up = up.parentElement) {
        if (d.display === "inline") lhPx = Math.max(lhPx, lhPxOf(d));
      }
      if (up) lhPx = Math.max(lhPx, lhPxOf(getComputedStyle(up)));
    }
    const lh = lhPx / size;
    const heading = !!el.closest("h1, h2, h3, h4, h5, h6") || size >= 24;
    const min = heading ? 1.3 : 1.6;
    if (lh + 1e-6 < min) {
      // Count text lines from the element's text nodes only (icons and
      // badges inside an inline-flex row are not lines), clustering line
      // boxes whose tops are closer than half a line apart.
      /** @type {number[]} */
      const tops = [];
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      // A descendant block lays out its own lines with its own leading; only text in this
      // element's line boxes (directly or through inline boxes) counts.
      const inOwnLines = (/** @type {Node} */ n) => {
        for (let a = n.parentElement; a && a !== el; a = a.parentElement) {
          if (!["inline", "contents"].includes(getComputedStyle(a).display)) return false;
        }
        return true;
      };
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!n.textContent?.trim() || !inOwnLines(n)) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const r of range.getClientRects()) if (r.width > 0) tops.push(r.top);
      }
      tops.sort((a, b) => a - b);
      let lines = 0;
      let last = -Infinity;
      for (const t of tops) if (t - last > lhPx / 2) { lines++; last = t; }
      if (lines >= 2) out.typo.push({ el: describe(el), detail: `line-height ${lh.toFixed(2)} < ${min} (${heading ? "heading" : "body"}, ${lines} lines)` });
    }
    const ls = cs.letterSpacing === "normal" ? 0 : parseFloat(cs.letterSpacing);
    if (ls < 0) out.typo.push({ el: describe(el), detail: `letter-spacing ${cs.letterSpacing} is negative` });
    const ctx = el.closest("[lang]")?.getAttribute("lang") ?? "";
    if (!/^th\b/i.test(ctx)) out.lang.push({ el: describe(el), detail: `Thai text in lang="${ctx}" context` });
  }
  return out;
}

function probeTargets() {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], [tabindex]:not([tabindex="-1"])';
  /** @type {Issue[]} */
  const bad = [];
  const describe = (/** @type {Element} */ el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + (el.textContent?.trim() ? ` "${el.textContent.trim().slice(0, 30)}"` : "");
  for (const el of /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll(sel))) {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 && r.height <= 1) continue; // not shown (e.g. sr-only skip link before focus)
    if (r.width >= 24 && r.height >= 24) continue;
    // A checkbox or radio is activated through its label too, so a label of 24×24 or more is its target.
    if (el instanceof HTMLInputElement && ["checkbox", "radio"].includes(el.type) && [...(el.labels ?? [])].some((l) => { const b = l.getBoundingClientRect(); return l.checkVisibility({ visibilityProperty: true }) && b.width >= 24 && b.height >= 24; })) continue;
    // Inline exception (2.5.8): a link inside a sentence. Only the parent's own
    // words count, not the text of sibling links, so a row of links joined by
    // separators is not a sentence however long it is.
    if (el.tagName === "A" && cs.display === "inline" && el.parentElement) {
      const parent = el.parentElement;
      let words = "";
      const walker = document.createTreeWalker(parent, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const owner = n.parentElement?.closest(sel);
        if (!owner || !parent.contains(owner)) words += n.textContent;
      }
      if ((words.match(/\p{L}/gu) ?? []).length >= 3) continue;
    }
    bad.push({ el: describe(el), detail: `${Math.round(r.width)}×${Math.round(r.height)} px` });
  }
  return bad;
}

// Records each focusable element's outline (with its offset) and box-shadow before any Tab, so
// probeFocus can tell a focus indicator from a shadow the element always has.
function probeRestStyles() {
  /** @type {WeakMap<Element, { outline: string, shadow: string }>} */
  const rest = new WeakMap();
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    rest.set(el, { outline: `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor} ${cs.outlineOffset}`, shadow: cs.boxShadow });
  }
  window.__designFloorRest = rest;
}

async function probeFocus() {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  // Read the settled state: a focus ring under `transition-all` starts transparent, and
  // `scroll-behavior: smooth` is still bringing the element into view right after Tab.
  const sleep = (/** @type {number} */ ms) => new Promise((r) => setTimeout(r, ms));
  await Promise.race([
    Promise.all(el.getAnimations().filter((a) => a.effect?.getComputedTiming?.().iterations !== Infinity).map((a) => a.finished.catch(() => {}))),
    sleep(1000),
  ]);
  const pos = () => { const b = el.getBoundingClientRect(); return `${b.left},${b.top}`; };
  for (let i = 0, last = pos(), still = 0; i < 30 && still < 3; i++) {
    await sleep(50);
    const now = pos();
    still = now === last ? still + 1 : 0;
    last = now;
  }
  const cs = getComputedStyle(el);
  // A fully transparent colour paints nothing (Tailwind's outline-hidden, the 0 0 #0000 ring reset).
  const clear = (/** @type {string} */ c) => c === "transparent" || /^rgba\([^)]*,\s*0(\.0+)?\)$|\/\s*0(\.0+)?\)$/.test(c.trim());
  const shadows = cs.boxShadow === "none" ? [] : cs.boxShadow.split(/,(?![^(]*\))/);
  const paints = (/** @type {string} */ p) => {
    const color = p.match(/(rgba?|hsla?|oklch|oklab|lab|lch|color)\([^)]*\)/)?.[0] ?? "";
    return !(color && clear(color)) && /[1-9]/.test(p.replace(color, ""));
  };
  // The offset counts too: a ring that only moves out from the edge on focus is a visible change.
  const outline = `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor} ${cs.outlineOffset}`;
  const rest = window.__designFloorRest?.get(el) ?? { outline: "", shadow: "" };
  const outlineRing = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0 && !clear(cs.outlineColor) && outline !== rest.outline;
  const shadowRing = shadows.some(paints) && cs.boxShadow !== rest.shadow;
  // A control faded out (by itself or an ancestor) hides its indicator with it.
  let opacity = 1;
  for (let /** @type {Element | null} */ a = el; a; a = a.parentElement) opacity *= parseFloat(getComputedStyle(a).opacity);
  const transparent = opacity < 0.1;
  const ring = (outlineRing || shadowRing) && !transparent;
  const r = el.getBoundingClientRect();
  /** @type {Array<[number, number]>} */
  const pts = [[0.5, 0.5], [0.15, 0.15], [0.85, 0.15], [0.15, 0.85], [0.85, 0.85]].map(([x, y]) => [r.left + r.width * x, r.top + r.height * y]);
  // The browser scrolls a focused element into view, so a point still outside
  // the viewport is one nobody can see; it counts as hidden like a covered one.
  const outside = (/** @type {[number, number]} */ [x, y]) => x < 0 || y < 0 || x >= innerWidth || y >= innerHeight;
  const offscreen = pts.every(outside);
  const covered = pts.every((p) => {
    if (outside(p)) return true;
    const hit = document.elementFromPoint(p[0], p[1]);
    return hit && hit !== el && !el.contains(hit) && !hit.contains(el);
  });
  // A skip link points at the main content: <main>/[role=main] itself, an
  // element that wraps it, or the first heading or first child inside it.
  const href = el.getAttribute("href") ?? "";
  let skipTarget = false;
  if (href.startsWith("#") && href.length > 1) {
    const t = document.getElementById(decodeURIComponent(href.slice(1)));
    const main = document.querySelector("main, [role=main]");
    skipTarget = !!(t && main && (t === main || t.contains(main) || t === main.firstElementChild || t === main.querySelector("h1")));
  }
  const key = el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + `@${Math.round(r.left)},${Math.round(r.top + scrollY)}`;
  // Tab moves inside a frame while the outer document keeps the <iframe> as its focus.
  const inFrame = el.tagName === "IFRAME" || el.tagName === "FRAME";
  return { key, ring, transparent, covered, offscreen, inFrame, isSkip: el.tagName === "A" && skipTarget, label: el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + (el.textContent?.trim() ? ` "${el.textContent.trim().slice(0, 30)}"` : "") };
}

// WCAG 2.3.3 covers motion: movement, scaling, rotation, size. Fades and
// colour changes are not motion, so animations that only touch those pass.
// Runs as an init script: CSS animations and transitions are recorded as they
// start, so one that is over before the probe reads the page still counts.
/** @param {number} limit */
function recordMotion(limit) {
  // Any *-color property counts as colour: getKeyframes() expands shorthands into longhands
  // (border-color -> borderTopColor...) and names them in camelCase; transitionProperty is hyphenated.
  const quiet = /^(opacity|fill|stroke|visibility|offset|easing|composite|computed-offset|([a-z]+-)*color)$/i;
  const hyphenate = (/** @type {string} */ p) => p.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
  const seen = new WeakSet();
  /** @type {Array<{ name: string, duration: unknown, iterations: unknown, el: string }>} */
  const found = [];
  /** @param {Animation & { animationName?: string, transitionProperty?: string }} a CSSAnimation or CSSTransition */
  const take = (a) => {
    // Only a reported animation is remembered: one that passed may be retimed later
    // (updateTiming to infinite iterations) and is read again at probe time.
    if (seen.has(a) || a.playState !== "running") return;
    const effect = /** @type {KeyframeEffect | null} */ (a.effect);
    const frames = effect?.getKeyframes?.() ?? [];
    // Only properties whose value changes count: a fade that also repeats one `transform` at
    // every keyframe moves nothing. A property is constant only when it is set at both ends
    // (offsets 0 and 1) and every keyframe that sets it agrees; an end it is missing from
    // takes the element's own value, so `to { transform: … }` alone does move.
    const at = (/** @type {number} */ offset) => frames.find((f) => f.computedOffset === offset);
    const start = at(0);
    const end = at(1);
    const props = new Set(frames.flatMap((f) => Object.keys(f)).filter((p) => {
      if (!start || !end || !(p in start) || !(p in end)) return true;
      const values = frames.filter((f) => p in f).map((f) => String(/** @type {Record<string, unknown>} */ (f)[p]));
      return new Set(values).size > 1;
    }));
    if (a.transitionProperty) props.add(a.transitionProperty);
    if (![...props].some((p) => !quiet.test(hyphenate(p)))) return;
    /** @type {ComputedEffectTiming} */
    const t = effect?.getComputedTiming?.() ?? {};
    // activeDuration covers every iteration: ten 150 ms slides are 1.5 s of motion.
    const total = typeof t.activeDuration === "number" ? t.activeDuration : t.duration;
    if (!(t.iterations === Infinity || (typeof total === "number" && total > limit))) return;
    seen.add(a);
    const target = effect?.target;
    found.push({ name: a.animationName ?? a.transitionProperty ?? a.constructor.name, duration: total, iterations: t.iterations, el: target ? target.tagName.toLowerCase() + (target.id ? `#${target.id}` : "") : "?" });
  };
  for (const type of ["animationstart", "transitionstart"]) {
    addEventListener(type, (e) => /** @type {Element | null} */ (e.target)?.getAnimations?.().forEach(take), true);
  }
  // Web Animations started from script (element.animate) fire no event: record each as it is
  // created, so a short one is counted after it has finished; the rest are read at probe time.
  const animate = Element.prototype.animate;
  /** @type {Element["animate"]} */
  Element.prototype.animate = function (keyframes, options) {
    const animation = animate.call(this, keyframes, options);
    queueMicrotask(() => take(animation));
    return animation;
  };
  window.__designFloorMotion = () => { document.getAnimations().forEach(take); return found; };
}

// Motion driven from script (requestAnimationFrame loops, libraries that write inline styles)
// is invisible to getAnimations(). Sample every element's transform and position for a second;
// one that changes in more samples than the limit covers moves under reduced motion (idle gaps
// between two short moves do not add up). Elements that are not rendered are not sampled.
/** @param {number} limit */
async function probeScriptedMotion(limit) {
  const els = [...document.body.querySelectorAll("*")].slice(0, 3000);
  // Same reading as recordMotion: a property moves when its value changes or an end leaves it
  // implicit; opacity, colour and visibility are not motion.
  const quiet = /^(opacity|offset|easing|composite|computedOffset|visibility|fill|stroke|.*[cC]olor)$/;
  const moves = (/** @type {ComputedKeyframe[]} */ frames) => {
    const start = frames.find((f) => f.computedOffset === 0);
    const end = frames.find((f) => f.computedOffset === 1);
    return [...new Set(frames.flatMap((f) => Object.keys(f)))].some((p) => !quiet.test(p)
      && (!start || !end || !(p in start) || !(p in end)
        || new Set(frames.filter((f) => p in f).map((f) => String(/** @type {Record<string, unknown>} */ (f)[p]))).size > 1));
  };
  // Boxes are compared to 0.01 px: a slow drift moves less than a pixel between samples.
  const sample = () => {
    // Elements under a finite moving CSS or Web Animation, themselves or through an ancestor,
    // are timed exactly by recordMotion (200 ms is allowed), so their movement is not counted here.
    const animated = new Set(document.getAnimations().filter((a) => a.playState === "running"
      && a.effect?.getComputedTiming().iterations !== Infinity
      && moves(/** @type {KeyframeEffect} */ (a.effect).getKeyframes()))
      .map((a) => /** @type {KeyframeEffect} */ (a.effect).target).filter((t) => t instanceof Element));
    return els.map((el) => {
    if (!el.checkVisibility()) return "hidden";
    // The element itself is animated: left to recordMotion. Under an animated ancestor, any box
    // reading moves with the ancestor (its transform, or its own animated size and position), so
    // only what script motion writes is read: the element's own transform and its inline geometry.
    if (animated.has(el)) return "animated";
    let under = false;
    for (let up = el.parentElement; up && !under; up = up.parentElement) under = animated.has(up);
    const c = getComputedStyle(el);
    const own = `${c.transform}|${c.translate}|${c.scale}|${c.rotate}`;
    if (under) {
      const st = /** @type {HTMLElement} */ (el).style;
      return `inline|${own}|${st ? [st.left, st.top, st.right, st.bottom, st.inset, st.width, st.height, st.margin, st.marginLeft, st.marginTop].join(",") : ""}`;
    }
    const r = el.getBoundingClientRect();
    return `box|${own}|${r.left.toFixed(2)},${r.top.toFixed(2)},${r.width.toFixed(2)},${r.height.toFixed(2)}`;
    });
  };
  const step = 50;
  /** @type {boolean[][]} */
  const changed = els.map(() => []);
  let prev = sample();
  for (let t = step; t <= 1000; t += step) {
    await new Promise((r) => setTimeout(r, step));
    const next = sample();
    next.forEach((v, i) => {
      // Entering or leaving a marker ("hidden", "animated") is not movement.
      // Switching between a marker or between the two ways of reading the box is not movement.
      const kind = (/** @type {string} */ x) => x.split("|")[0];
      changed[i].push(v !== prev[i] && !/^(hidden|animated)$/.test(v) && !/^(hidden|animated)$/.test(prev[i]) && kind(v) === kind(prev[i]));
    });
    prev = next;
  }
  const describe = (/** @type {Element} */ el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "");
  // Report the outermost moving element only: its children move with it.
  // A tween drawn at a low frame rate changes every other sample: gaps of up to two samples
  // between changes still count as one run, a longer gap ends it. The longest run is the
  // motion's duration (two separate short moves are not added up), less one sample, because a
  // run of n changed samples spans between (n - 1) and n sample steps.
  const movingTime = (/** @type {boolean[]} */ flags) => {
    let longest = 0;
    let run = 0;
    let last = -Infinity;
    flags.forEach((on, k) => {
      if (!on) return;
      run = k - last <= 3 ? run + (k - last) * step : step;
      longest = Math.max(longest, run - step);
      last = k;
    });
    return longest;
  };
  const moving = els.filter((_, i) => movingTime(changed[i]) > limit);
  return moving.filter((el) => !moving.some((other) => other !== el && other.contains(el))).slice(0, 5).map(describe);
}

// Entrance animations (fades, slides) are measured mid-flight otherwise: axe
// then reports contrast of half-transparent text. Wait for finite ones.
// Scroll-triggered entrances (whileInView, IntersectionObserver) keep
// below-the-fold content transparent, and axe skips transparent text. Scroll
// the page through once so everything is revealed, then return to the top.
/** @param {Page} page */
async function revealAll(page) {
  await page.evaluate(async (maxSteps) => {
    const step = Math.max(200, Math.floor(innerHeight * 0.8));
    // "instant" overrides the page's scroll-behavior: smooth, which would otherwise still be
    // animating (and interrupted by the next step) when lower content should come into view.
    for (let y = 0, i = 0; y < document.documentElement.scrollHeight && i < maxSteps; y += step, i++) {
      scrollTo({ top: y, behavior: "instant" });
      await new Promise((r) => setTimeout(r, 60));
    }
    scrollTo({ top: 0, behavior: "instant" });
  }, MAX_SCROLL_STEPS);
}

// JS-driven animation (framer-motion, GSAP) is invisible to getAnimations(),
// so also wait until opacity and transforms stop changing.
/**
 * @param {Page} page
 * @param {number} [ms]
 */
async function settleAnimations(page, ms = 3000) {
  await page.evaluate((limit) => Promise.race([
    Promise.all(document.getAnimations()
      .filter((a) => a.effect?.getComputedTiming?.().iterations !== Infinity)
      .map((a) => a.finished.catch(() => {}))),
    new Promise((r) => setTimeout(r, limit)),
  ]), ms);
  await page.evaluate(async (limit) => {
    const snap = () => [...document.body.querySelectorAll("*")].slice(0, 3000)
      .map((el) => { const c = getComputedStyle(el); return c.opacity + c.transform; }).join("|");
    const end = Date.now() + limit;
    let prev = snap();
    while (Date.now() < end) {
      await new Promise((r) => setTimeout(r, 250));
      const next = snap();
      if (next === prev) return;
      prev = next;
    }
  }, ms);
}

// Asks Chromium which fonts actually drew each tagged Thai element. Latin-first
// stacks ("Inter", "Noto Sans Thai", …) pass because Noto draws the Thai glyphs.
/**
 * @param {Page} page
 * @returns {Promise<Issue[]>}
 */
async function renderedThaiFonts(page) {
  // CDP reports the fonts of a whole element, so in mixed text a Noto Latin subset could hide a
  // system fallback that drew every Thai character. Each tagged element gets a child run holding
  // only its Thai characters, laid out off screen in the same font, and that run is what is asked.
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("[data-design-floor-thai]")) {
      // A form control's text is what it shows: a button's label, or a text field's or textarea's value, or its placeholder while empty.
      const own = el instanceof HTMLTextAreaElement ? el.value || el.placeholder : el instanceof HTMLInputElement ? (/^(submit|reset|button)$/.test(el.type) ? el.value
        : /^(text|search|url|tel|email|number)$/.test(el.type) ? el.value || el.placeholder : "")
        : [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("");
      const run = document.createElement("span");
      run.setAttribute("data-design-floor-thai-run", el.getAttribute("data-design-floor-thai") ?? "");
      run.setAttribute("aria-hidden", "true");
      // Copy the element's own font so the page's rules for spans (`p > span { font-family: … }`)
      // cannot give the probe a different one; !important also beats the page's !important rules.
      // A placeholder is drawn with the ::placeholder styles, which may set their own font.
      const cs = getComputedStyle(el, (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && !el.value ? "::placeholder" : null);
      for (const prop of ["font-family", "font-weight", "font-style", "font-stretch", "font-size", "font-variant",
        "font-feature-settings", "font-variation-settings", "font-synthesis", "font-kerning", "font-language-override"]) {
        const value = cs.getPropertyValue(prop);
        if (value) run.style.setProperty(prop, value, "important");
      }
      for (const [prop, value] of [["position", "absolute"], ["left", "-100000px"], ["top", "0"], ["white-space", "nowrap"],
        ["visibility", "visible"], ["display", "inline"]]) run.style.setProperty(prop, value, "important");
      // Joined with nothing: a space between the pieces is drawn by the first font in the stack
      // (Geist, Inter...), which would read as a fallback although it draws no Thai.
      run.textContent = (own.match(/[\u0E00-\u0E7F]+/g) ?? []).join("");
      // A child of an input, textarea, select or option is never rendered: lay the run out in <body>
      // instead, in the control's language, which steers Chromium's fallback choice.
      if (el.matches("input, textarea, select, option, optgroup")) {
        run.lang = /** @type {HTMLElement | null} */ (el.closest("[lang]"))?.lang ?? "";
        document.body.append(run);
      } else el.append(run);
    }
  });
  const cdp = await page.context().newCDPSession(page);
  try {
    await cdp.send("DOM.enable");
    await cdp.send("CSS.enable");
    const { root } = await cdp.send("DOM.getDocument", { depth: -1 });
    const { nodeIds } = await cdp.send("DOM.querySelectorAll", { nodeId: root.nodeId, selector: "[data-design-floor-thai-run]" });
    /** @type {Issue[]} */
    const bad = [];
    for (const nodeId of nodeIds) {
      const { attributes } = await cdp.send("DOM.getAttributes", { nodeId });
      const label = attributes[attributes.indexOf("data-design-floor-thai-run") + 1];
      const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
      const drew = fonts.filter((f) => f.glyphCount > 0);
      if (!drew.length) continue; // nothing laid out
      // Every Thai glyph must come from Noto Sans/Serif Thai: any other font with glyphs here is a fallback.
      if (drew.some((f) => !/noto\s*(sans|serif)\s*thai/i.test(f.familyName))) {
        bad.push({ el: label, detail: `rendered with ${fonts.map((f) => f.familyName).join(", ") || "no font"}` });
      }
    }
    return bad;
  } finally {
    await cdp.detach();
    await page.evaluate(() => document.querySelectorAll("[data-design-floor-thai-run]").forEach((n) => n.remove()));
  }
}

// ---------- run ----------

/**
 * @param {string} rule
 * @param {string} route
 * @param {number} width
 * @param {string} el
 * @param {string} detail
 * @returns {Finding}
 */
function finding(rule, route, width, el, detail) { return { rule, route, width, el, detail }; }

// axe rules that check a floor row of their own report under that row, so its waiver covers them.
/** @param {string} id */
export function axeRule(id) {
  if (id === "color-contrast") return "contrast";
  if (id === "target-size") return "target-size";
  return AXE_LANG_RULES.has(id) ? "lang" : "axe";
}

/**
 * @param {Config} cfg
 * @param {{ shots?: string, launch?: () => Promise<import("playwright").Browser> }} [options]
 * @returns {Promise<Finding[]>}
 */
export async function runChecks(cfg, { shots, launch = () => chromium.launch() } = {}) {
  const target = await startTarget(cfg);
  /** @type {import("playwright").Browser | undefined} */
  let browser;
  /** @type {Finding[]} */
  const findings = [];
  try {
    browser = await launch();
    for (const [index, { path: route, status: expected }] of cfg.routes.entries()) {
      const url = new URL(route, target.url).href;

      for (const width of cfg.widths) {
        const ctx = await browser.newContext({ viewport: { width, height: HEIGHT } });
        const page = await ctx.newPage();
        const resp = await page.goto(url, { waitUntil: "load" });
        const got = resp?.status();
        if (!resp || got === undefined || (expected ? got !== expected : got >= 400)) throw new Error(`${url} returned ${got}${expected ? `, expected ${expected}` : ""}`);
        await page.evaluate(() => document.fonts.ready);
        await revealAll(page);
        await settleAnimations(page);

        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        if (overflow > 1) findings.push(finding("reflow", route, width, "html", `horizontal scroll of ${overflow} px`));

        if (width === 390 || width === 1440) {
          // @axe-core/playwright declares its own copy of playwright's Page type; the object is the same.
          const axe = await new AxeBuilder({ page: /** @type {any} */ (page) }).withTags(AXE_TAGS).analyze();
          for (const v of axe.violations) {
            const rule = axeRule(v.id);
            for (const n of v.nodes.slice(0, 5)) findings.push(finding(rule, route, width, n.target.join(" "), `${v.id}: ${v.help}`));
          }
        }

        if (width === 390) {
          const t = await page.evaluate(probeTypography);
          if (t.langMissing) findings.push(finding("lang", route, width, "html", "<html> has no lang attribute"));
          for (const f of await renderedThaiFonts(page)) findings.push(finding("thai-font", route, width, f.el, f.detail));
          for (const f of t.typo) findings.push(finding("thai-typography", route, width, f.el, f.detail));
          for (const f of t.lang) findings.push(finding("lang", route, width, f.el, f.detail));
          for (const f of await page.evaluate(probeTargets)) findings.push(finding("target-size", route, width, f.el, f.detail));
        }

        if (width === 1440) {
          await page.evaluate(probeRestStyles);
          await page.keyboard.press("Tab");
          const first = await page.evaluate(probeFocus);
          if (!first?.isSkip) findings.push(finding("skip-link", route, width, first?.label ?? "(nothing focused)", "first Tab stop is not a skip link to an existing target"));
          let state = first;
          const seen = new Set();
          const order = [];
          // Stops already judged for focus-visible/obscured, kept across an Escape that restarts the walk.
          const judged = new Set();
          let escapes = 0;
          // After Escape lets focus out of a dialog or a control that held Tab, the walk carries on
          // from where focus landed, so the controls after it are still checked (up to 3 times).
          /** @param {typeof first} after */
          const resume = (after) => {
            if (!after || escapes >= 3) return false;
            escapes++;
            seen.clear();
            order.length = 0;
            state = after;
            return true;
          };
          for (let i = 0; i < MAX_TAB_STOPS && state && !seen.has(state.key); i++) {
            seen.add(state.key);
            order.push(state.key);
            const fresh = !judged.has(state.key);
            judged.add(state.key);
            // The ring of a control inside a frame is drawn in the frame's own document, which may be
            // cross-origin; FLOOR.md lists frame content as a manual check.
            if (fresh && !state.ring && !state.inFrame) findings.push(finding("focus-visible", route, width, state.label, state.transparent ? "focused element is transparent (opacity)" : "no outline or box-shadow while focused"));
            if (fresh && state.covered) findings.push(finding("focus-not-obscured", route, width, state.label, state.offscreen ? "focused element is outside the viewport" : "focused element is fully covered"));
            let next = null;
            let stuck = 0;
            // Inside a frame, Tab walks the frame's own controls first: give it the full stop
            // budget to come back out before calling it stuck.
            const limit = state.inFrame ? MAX_TAB_STOPS : 3;
            for (; stuck < limit; stuck++) {
              await page.keyboard.press("Tab");
              next = await page.evaluate(probeFocus);
              if (!next || next.key !== state.key) break;
            }
            if (stuck === limit) {
              // As with a cycle below: a control that holds Tab on purpose may let Escape out.
              await page.keyboard.press("Escape");
              await page.keyboard.press("Tab");
              const after = await page.evaluate(probeFocus);
              if (after && after.key === state.key) findings.push(finding("keyboard-trap", route, width, state.label, "focus did not move after 3 Tabs or Escape"));
              else if (resume(after)) continue;
              break;
            }
            // Focus back on a stop it already visited, without passing through the page
            // (a real wrap goes via the browser UI, where nothing is focused), circles a group.
            // It is a trap only when the page has tabbable controls the group never reached
            // (a small page may wrap straight back), and only if Escape does not break it
            // (a modal dialog holds focus on purpose and is left with Escape).
            if (next && seen.has(next.key)) {
              const cycle = new Set(order.slice(order.indexOf(next.key)));
              const tabbable = await page.evaluate(() => [...document.querySelectorAll(
                'a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"])',
              )].filter((el) => !(/** @type {HTMLElement} */ (el)).matches(":disabled, [inert] *") && (/** @type {HTMLElement} */ (el)).tabIndex >= 0
                && el.getClientRects().length > 0 && getComputedStyle(el).visibility === "visible").length);
              if (cycle.size >= tabbable) break; // every control was reached: a wrap, not a trap
              await page.keyboard.press("Escape");
              await page.keyboard.press("Tab");
              const after = await page.evaluate(probeFocus);
              if (after && cycle.has(after.key)) findings.push(finding("keyboard-trap", route, width, next.label, "focus cycles through a group of elements that Escape does not leave"));
              else if (resume(after)) continue;
              break;
            }
            state = next;
          }
        }

        if (shots) {
          await mkdir(shots, { recursive: true });
          // The route's position keeps names unique: "/a-b" and "/a/b" (or Thai paths) share a slug.
          const slug = route.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "root";
          const name = `${String(index + 1).padStart(2, "0")}-${slug}-${width}.png`;
          await page.screenshot({ path: path.join(shots, name), fullPage: true });
        }
        await ctx.close();
      }

      const ctx = await browser.newContext({ viewport: { width: 390, height: HEIGHT }, reducedMotion: "reduce" });
      await ctx.addInitScript(recordMotion, MOTION_LIMIT_MS);
      const page = await ctx.newPage();
      await page.goto(url, { waitUntil: "load" });
      // Scroll-triggered entrances start only here, so reveal the page under reduced motion too.
      await revealAll(page);
      await page.waitForTimeout(300);
      // Sample script motion first: animations that start during the sample are then recorded too.
      for (const el of await page.evaluate(probeScriptedMotion, MOTION_LIMIT_MS)) {
        findings.push(finding("reduced-motion", route, 390, el, "moves from script (requestAnimationFrame or inline styles) under reduced motion"));
      }
      for (const a of await page.evaluate(() => window.__designFloorMotion?.() ?? [])) {
        findings.push(finding("reduced-motion", route, 390, a.el, `${a.name} runs ${a.iterations === Infinity ? "infinitely" : `${Math.round(Number(a.duration))} ms`} under reduced motion`));
      }
      await ctx.close();
    }
  } finally {
    // Stop the target even when Chromium never launched, or a served app outlives the run.
    await browser?.close();
    await target.stop();
  }
  return findings;
}

/**
 * @param {Config} cfg
 * @param {Finding[]} findings
 * @param {string} today YYYY-MM-DD
 * @returns {Result}
 */
export function applyWaivers(cfg, findings, today) {
  /** @type {string[]} */
  const problems = [];
  /** @type {Waiver[]} */
  const active = [];
  for (const w of cfg.waivers) {
    if (cfg.public && NOT_WAIVABLE_ON_PUBLIC.has(w.rule)) { problems.push(`waiver for ${w.rule} not allowed on a public app`); continue; }
    if (w.expires < today) { problems.push(`waiver for ${w.rule} expired on ${w.expires}`); continue; }
    active.push(w);
  }
  const covers = (/** @type {Waiver} */ w, /** @type {Finding} */ f) => w.rule === f.rule && (!w.routes || w.routes.includes(f.route));
  /** @type {Finding[]} */
  const failed = [];
  /** @type {Finding[]} */
  const waived = [];
  for (const f of findings) (active.some((w) => covers(w, f)) ? waived : failed).push(f);
  return { failed, waived, problems };
}

/**
 * @param {Config} cfg
 * @param {Result} result
 */
function summarize(cfg, result) {
  const lines = [];
  lines.push(`design floor ${FLOOR_VERSION} — automated part only; not a WCAG 2.2 AA conformance claim.`);
  lines.push(`manual rows still to sign off in ux-review: ${MANUAL_ROWS.join(", ")}.`);
  const byRule = new Map(RULES.map((r) => [r, { failed: 0, waived: 0 }]));
  // Every finding's rule is one of RULES, so the counter exists.
  for (const f of result.failed) /** @type {{ failed: number }} */ (byRule.get(f.rule)).failed++;
  for (const f of result.waived) /** @type {{ waived: number }} */ (byRule.get(f.rule)).waived++;
  for (const [rule, c] of byRule) lines.push(`  ${c.failed ? "FAIL" : c.waived ? "WAIVED" : "PASS"}  ${rule}${c.failed ? ` (${c.failed})` : ""}${c.waived ? ` [${c.waived} waived]` : ""}`);
  for (const p of result.problems) lines.push(`  FAIL  waiver: ${p}`);
  for (const f of result.failed.slice(0, 50)) lines.push(`    - ${f.rule} ${f.route} @${f.width}: ${f.el} — ${f.detail}`);
  if (result.failed.length > 50) lines.push(`    … ${result.failed.length - 50} more in the JSON report`);
  return lines.join("\n");
}

/** @param {string[]} argv */
async function main(argv) {
  /** @type {{ config: string, json?: string, shots?: string, today?: string }} */
  const args = { config: "design-floor.json" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--config") args.config = argv[++i];
    else if (a === "--json") args.json = argv[++i];
    else if (a === "--shots") args.shots = argv[++i];
    else if (a === "--today") args.today = argv[++i];
    else { console.error(`unknown argument ${a}`); return 2; }
  }
  let cfg;
  try { cfg = await loadConfig(args.config); } catch (e) { console.error(`config error: ${e instanceof Error ? e.message : e}`); return 2; }
  const today = args.today ?? new Date().toISOString().slice(0, 10);
  let findings;
  try { findings = await runChecks(cfg, { shots: args.shots }); } catch (e) { console.error(`run error: ${e instanceof Error ? e.message : e}`); return 2; }
  const result = applyWaivers(cfg, findings, today);
  console.log(summarize(cfg, result));
  if (args.json) {
    await writeFile(args.json, JSON.stringify({ floor: FLOOR_VERSION, date: today, conformanceClaim: false, manualRows: MANUAL_ROWS, ...result }, null, 2));
  }
  return result.failed.length || result.problems.length ? 1 : 0;
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("check.mjs")) {
  main(process.argv.slice(2)).then((code) => process.exit(code));
}
