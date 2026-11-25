// Writes fixtures/<name>/index.html + design-floor.json: one page that meets the
// floor ("good") and one page per defect that should fail exactly one rule.
// Run after editing a fixture: node test/build-fixtures.mjs
import { mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FONT = "/node_modules/@fontsource/noto-sans-thai/files";

/**
 * @typedef {{ lang?: string, style?: string, skip?: boolean, extra?: string, script?: string, useFont?: boolean, theme?: string, head?: string }} PageOptions
 */

/** @param {PageOptions} [options] */
function page({ lang = "th", style = "", skip = true, extra = "", script = "", useFont = true, theme = "", head = "" } = {}) {
  const fontFaces = useFont ? `
    @font-face { font-family: "Noto Sans Thai"; font-weight: 400; font-display: swap;
      src: url("${FONT}/noto-sans-thai-thai-400-normal.woff2") format("woff2"); unicode-range: U+0E01-0E5B, U+200C-200D, U+25CC; }
    @font-face { font-family: "Noto Sans Thai"; font-weight: 400; font-display: swap;
      src: url("${FONT}/noto-sans-thai-latin-400-normal.woff2") format("woff2"); unicode-range: U+0000-00FF; }
    @font-face { font-family: "Noto Sans Thai"; font-weight: 700; font-display: swap;
      src: url("${FONT}/noto-sans-thai-thai-700-normal.woff2") format("woff2"); unicode-range: U+0E01-0E5B, U+200C-200D, U+25CC; }` : "";
  return `<!doctype html>
<html${lang ? ` lang="${lang}"` : ""}${theme ? ` data-theme="${theme}"` : ""}>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ร้านหนังสือตัวอย่าง</title>
  <link rel="stylesheet" href="/tokens.css">
${head}  <style>${fontFaces}
    body { margin: 0; background: var(--color-bg); color: var(--color-fg); font-family: var(--font-sans); line-height: var(--leading-thai); }
    h1, h2 { line-height: var(--leading-thai-heading); }
    .skip { position: absolute; left: var(--space-2); top: -4rem; padding: var(--space-2) var(--space-4); background: var(--color-accent); color: var(--color-accent-fg); }
    .skip:focus { top: var(--space-2); }
    header nav { display: flex; gap: var(--space-4); padding: var(--space-4); }
    header nav a { display: inline-block; padding: var(--space-2) var(--space-3); color: var(--color-accent); }
    main { max-width: 40rem; margin: 0 auto; padding: var(--space-6) var(--space-4); }
    a { color: var(--color-accent); }
    button { min-height: var(--target-touch); padding: 0 var(--space-4); border: 0; border-radius: var(--radius-md); background: var(--color-accent); color: var(--color-accent-fg); font: inherit; }
    .badge { display: inline-block; animation: pulse var(--duration-slow) var(--ease-out) 1; }
    @keyframes pulse { from { opacity: 0.4; } to { opacity: 1; } }
    footer { padding: var(--space-6) var(--space-4); color: var(--color-fg-muted); }
${style}
  </style>
</head>
<body>
  ${skip ? '<a class="skip" href="#main">ข้ามไปยังเนื้อหาหลัก</a>' : ""}
  <header>
    <nav aria-label="เมนูหลัก">
      <a href="#books">หนังสือ</a>
      <a href="#about">เกี่ยวกับเรา</a>
    </nav>
  </header>
  <main id="main">
    <h1>ร้านหนังสือตัวอย่าง</h1>
    <p>หน้านี้ใช้ทดสอบเกณฑ์คุณภาพขั้นต่ำของการออกแบบ อ่าน<a href="#about">รายละเอียดเพิ่มเติม</a>ได้ที่ส่วนท้ายของหน้า</p>
    <p lang="en">This sentence is English and carries its own language tag.</p>
    <h2 id="books">หนังสือแนะนำ <span class="badge">ใหม่</span></h2>
    <p>หนังสือเล่มนี้เหมาะสำหรับผู้เริ่มต้นที่อยากเข้าใจพื้นฐานการออกแบบที่เข้าถึงได้</p>
    <button type="button">เพิ่มลงตะกร้า</button>
    <h2 id="about">เกี่ยวกับเรา</h2>
    <p>เราเป็นร้านหนังสืออิสระขนาดเล็ก</p>
${extra}
  </main>
  <footer>สงวนลิขสิทธิ์ ร้านหนังสือตัวอย่าง</footer>
${script ? `<script>${script}</script>` : ""}
</body>
</html>
`;
}

// name -> [page options, rule expected to fail (null = none)]
/** @type {Record<string, [PageOptions, string | null]>} */
export const FIXTURES = {
  good: [{}, null],
  "good-dark": [{ theme: "dark" }, null],
  "good-book-brand": [{ head: '  <link rel="stylesheet" href="/presets/book-brand.css">\n' }, null],
  "good-book-brand-dark": [{ theme: "dark", head: '  <link rel="stylesheet" href="/presets/book-brand.css">\n' }, null],
  "good-shadcn-aliases": [{ head: '  <link rel="stylesheet" href="/aliases/shadcn.css">\n', style: "    button { background: var(--primary); color: var(--primary-foreground); }\n    main p { color: var(--muted-foreground); }" }, null],
  "no-thai-font": [{ useFont: false, style: "    body { font-family: system-ui, sans-serif; }" }, "thai-font"],
  // tokens.css resets letter-spacing on :lang(th), so the defect needs a more specific selector.
  "thai-tracking": [{ style: "    #main h1 { letter-spacing: -0.03em; }" }, "thai-typography"],
  // Nested display:contents still renders its Thai text, so the tracking rule must see it.
  "thai-tracking-contents": [{ extra: '<div style="display: contents"><span style="display: contents; letter-spacing: -0.05em">ข้อความในกล่องที่ไม่มีกรอบของตัวเอง</span></div>' }, "thai-typography"],
  // A visible child of a visibility:hidden parent renders, so the tracking rule must see it.
  "thai-tracking-visible-child": [{ extra: '<div style="visibility: hidden"><span style="display: contents; visibility: visible; letter-spacing: -0.05em">ข้อความที่ตั้งให้มองเห็นอีกครั้ง</span></div>' }, "thai-typography"],
  "thai-tight-leading": [{ style: "    main p { line-height: 1.3; }" }, "thai-typography"],
  // sr-only skip link (1×1 until focused) is not a small target; it still counts as the skip link.
  "good-sr-only-skip": [{ style: "    .skip { top: auto; left: auto; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }\n    .skip:focus { width: auto; height: auto; padding: var(--space-2) var(--space-4); clip-path: none; position: fixed; top: var(--space-2); left: var(--space-2); }" }, null],
  // An icon next to one line of Thai in an inline-flex chip is still one line.
  "good-icon-chip": [{ extra: '    <span class="chip" style="display:inline-flex;align-items:center;gap:4px;line-height:1.5;font-size:12px"><svg width="20" height="20" aria-hidden="true"><rect width="20" height="20"/></svg>ใหม่ล่าสุด</span>' }, null],
  // Single-line controls and inline runs may use tight leading: no stacked Thai lines.
  "good-tight-single-line": [{ style: "    button, header nav a { line-height: 1; }\n    main p a { line-height: 1.1; }" }, null],
  "no-lang": [{ lang: "" }, "lang"],
  "no-skip-link": [{ skip: false }, "skip-link"],
  "no-focus-ring": [{ style: "    a:focus-visible, button:focus-visible { outline: none; }" }, "focus-visible"],
  "focus-obscured": [{
    style: "    .bar { position: fixed; left: 0; right: 0; bottom: 0; height: 120px; background: var(--color-surface); }\n    .under { position: fixed; bottom: 40px; left: 50%; }",
    extra: '    <a class="under" href="#books">ลิงก์ที่ถูกบัง</a>\n    <div class="bar" aria-hidden="true"></div>',
  }, "focus-not-obscured"],
  "keyboard-trap": [{
    extra: '    <input aria-label="ค้นหา" id="trap" style="min-height:44px">',
    script: "document.getElementById('trap').addEventListener('keydown', (e) => { if (e.key === 'Tab') e.preventDefault(); });",
  }, "keyboard-trap"],
  "small-targets": [{
    extra: '    <button type="button" class="tiny" aria-label="ปิด">×</button>',
    style: "    .tiny { min-height: 0; width: 16px; height: 16px; padding: 0; }",
  }, "target-size"],
  "overflow-320": [{ extra: '    <div style="width: 340px; height: 8px; background: var(--color-border)"></div>' }, "reflow"],
  "motion-unguarded": [{ style: "    .badge { animation: spin 1.5s linear infinite; }\n    @keyframes spin { to { transform: rotate(360deg); } }" }, "reduced-motion"],
  // A finite fade-in must not be measured mid-flight by the contrast rule.
  "good-fade-in": [{ style: "    main { animation: fadein 800ms ease-out 1 both; }\n    @keyframes fadein { from { opacity: 0.2; } to { opacity: 1; } }\n    @media (prefers-reduced-motion: reduce) { main { animation: none; } }" }, null],
  // An infinite opacity pulse is not motion (WCAG 2.3.3), so it passes.
  "good-opacity-pulse": [{ style: "    .badge { animation: glow 1.2s ease-in-out infinite alternate; }\n    @keyframes glow { from { opacity: 0.6; } to { opacity: 1; } }" }, null],
  "low-contrast": [{ style: "    main p { color: #9a9a9a; }" }, "contrast"],
  // getKeyframes() names properties in camelCase (backgroundColor): a colour-only loop is not motion.
  "good-color-pulse": [{ style: "    .badge { animation: tint 1.2s ease-in-out infinite alternate; }\n    @keyframes tint { from { background-color: #eef; border-color: #ccd; } to { background-color: #fff; border-color: #eee; } }" }, null],
  // Tab cycles between two links and never reaches the rest of the page.
  "keyboard-trap-cycle": [{
    extra: '    <p><a id="loop-a" href="#books">วนรอบแรก</a> และ <a id="loop-b" href="#about">วนรอบสอง</a></p>',
    script: "document.getElementById('loop-b').addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); document.getElementById('loop-a').focus(); } });",
  }, "keyboard-trap"],
  // A slide that is over in 250 ms ends before the probe reads the page; it still ignores reduced motion.
  "motion-short-unguarded": [{ style: "    main h1 { animation: slidein 250ms ease-out 1 both; }\n    @keyframes slidein { from { transform: translateY(16px); } to { transform: none; } }" }, "reduced-motion"],
  // A shadow the button always has is not a focus indicator.
  "focus-permanent-shadow": [{ style: "    button { box-shadow: 0 2px 6px rgb(0 0 0 / 0.35); }\n    button:focus-visible { outline: none; }" }, "focus-visible"],
  // Tailwind's outline-hidden: an outline that paints nothing.
  "focus-transparent-outline": [{ style: "    button:focus-visible { outline: 2px solid transparent; }" }, "focus-visible"],
  // A ring drawn with box-shadow on focus is a focus indicator, even over a transparent reset shadow.
  "good-focus-shadow-ring": [{ style: "    button { box-shadow: 0 0 #0000; }\n    button:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--color-bg), 0 0 0 6px var(--color-focus); }" }, null],
  // A ring that fades in with a transition is read once it has settled, not at its transparent start.
  "good-focus-ring-transition": [{ style: "    button { box-shadow: 0 0 #0000; transition: all 300ms ease; }\n    button:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--color-focus); }" }, null],
  // An outline the button always has, moved out from its edge on focus, is a visible change.
  "good-focus-offset-ring": [{ style: "    button { outline: var(--focus-ring-width) solid var(--color-focus); outline-offset: -2px; }\n    button:focus-visible { outline-offset: 4px; }" }, null],
  // Smooth scrolling is still moving a far-down link into view right after Tab.
  "good-smooth-scroll": [{
    style: "    html { scroll-behavior: smooth; }",
    extra: '    <div style="height: 2400px"></div>\n    <p><a href="#main">กลับขึ้นด้านบนของหน้า</a></p>',
  }, null],
  // A link parked off screen is hidden when it takes focus.
  "focus-offscreen": [{
    style: "    .parked { position: absolute; left: -9999px; display: inline-block; padding: 12px; }",
    extra: '    <a class="parked" href="#books">ลิงก์นอกจอ</a>',
  }, "focus-not-obscured"],
  // Thai that wraps inside an inline span sits in the tight line box of its paragraph.
  "thai-tight-inline": [{ extra: '    <p style="line-height: 1.2"><span>ข้อความภาษาไทยที่ยาวพอจะขึ้นบรรทัดใหม่ในจอแคบ เพราะอยู่ในย่อหน้าที่ตั้งระยะบรรทัดไว้แน่นเกินไปสำหรับสระและวรรณยุกต์</span></p>' }, "thai-typography"],
  // A tall enclosing inline box makes the line box tall enough for a tight inner span.
  "good-thai-nested-inline": [{ extra: '    <p style="line-height: 1.2"><span style="line-height: 1.8"><span style="line-height: 1.2">ข้อความภาษาไทยที่ยาวพอจะขึ้นบรรทัดใหม่ในจอแคบ แต่กล่องแบบอินไลน์ด้านนอกตั้งระยะบรรทัดไว้กว้างพอสำหรับสระและวรรณยุกต์</span></span></p>' }, null],
  // A row of small links joined by separators is not a sentence, however long the row is.
  "small-targets-link-row": [{
    style: "    .row a { font-size: 11px; line-height: 1; }",
    extra: '    <p class="row"><a href="#books">หนังสือใหม่</a> · <a href="#about">เกี่ยวกับร้าน</a> · <a href="#main">ติดต่อเรา</a> · <a href="#books">นโยบายส่วนตัว</a></p>',
  }, "target-size"],
  // An infinite feed keeps growing as it scrolls; the reveal pass must still end.
  "good-infinite-feed": [{
    extra: '    <div id="feed"></div>',
    script: "const feed = document.getElementById('feed'); const more = () => { for (let i = 0; i < 5; i++) { const p = document.createElement('p'); p.textContent = 'รายการใหม่ในฟีดที่โหลดต่อเนื่อง'; p.style.height = '300px'; feed.append(p); } }; more(); addEventListener('scroll', () => { if (scrollY + innerHeight * 3 > document.documentElement.scrollHeight) more(); });",
  }, null],
  // A dialog cycles focus on purpose and closes with Escape: not a trap.
  "good-modal-escape": [{
    extra: '    <div role="dialog" aria-label="ตัวอย่างกล่องโต้ตอบ" id="dlg"><a id="loop-a" href="#books">ตัวเลือกแรก</a> <a id="loop-b" href="#about">ตัวเลือกสอง</a></div>',
    script: "document.getElementById('loop-b').addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); document.getElementById('loop-a').focus(); } }); addEventListener('keydown', (e) => { if (e.key === 'Escape') document.getElementById('dlg').remove(); });",
  }, null],
  // The walk carries on after Escape closes the dialog: the button after it has no focus ring.
  "focus-after-modal": [{
    extra: '    <div role="dialog" aria-label="ตัวอย่างกล่องโต้ตอบ" id="dlg"><a id="loop-a" href="#books">ตัวเลือกแรก</a> <a id="loop-b" href="#about">ตัวเลือกสอง</a></div>\n    <button type="button" id="late">ปุ่มท้าย</button>',
    style: "    #late:focus-visible { outline: none; box-shadow: none; }",
    script: "document.getElementById('loop-b').addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); document.getElementById('loop-a').focus(); } }); addEventListener('keydown', (e) => { if (e.key === 'Escape') document.getElementById('dlg').remove(); });",
  }, "focus-visible"],
  // Thai in a textarea is drawn by a system fallback; a probe appended inside it never renders.
  "thai-font-textarea": [{
    extra: '    <textarea aria-label="ข้อความ" style="font-family: monospace; min-height: 44px">ข้อความภาษาไทย</textarea>',
  }, "thai-font"],
  // Thai in an input's placeholder is drawn by a system fallback; an input has no text node.
  "thai-font-input": [{
    extra: '    <input aria-label="ค้นหา" placeholder="ค้นหาหนังสือ" style="font-family: monospace; min-height: 44px">',
  }, "thai-font"],
  // A requestAnimationFrame loop changes only a pinned bar's height: nothing else moves.
  "motion-scripted-height": [{
    extra: '    <div id="bar" aria-hidden="true" style="position: fixed; top: 0; right: 0; width: 8px; height: 40px; background: #1d5bbf"></div>',
    script: "const b = document.getElementById('bar'); const t0 = performance.now(); (function tick(t) { b.style.height = (40 + Math.round(30 * Math.abs(Math.sin((t - t0) / 150)))) + 'px'; requestAnimationFrame(tick); })(t0);",
  }, "reduced-motion"],
  // A 2 px nudge from script every 600 ms: single steps with idle gaps, not continuous motion.
  "good-scripted-nudges": [{ script: "const h = document.querySelector('main h1'); let on = false; setInterval(() => { on = !on; h.style.transform = on ? 'translateX(2px)' : 'none'; }, 600);" }, null],
  // A colour pulse (allowed) does not hide a requestAnimationFrame slide on the same element.
  "motion-scripted-with-color": [{
    style: "    @keyframes pulse { from { color: #1a1a1a; } to { color: #333; } }\n    main h1 { animation: pulse 1s infinite alternate; }",
    script: "const h = document.querySelector('main h1'); const t0 = performance.now(); (function tick(t) { h.style.transform = 'translateX(' + Math.round(10 * Math.sin((t - t0) / 150)) + 'px)'; requestAnimationFrame(tick); })(t0);",
  }, "reduced-motion"],
  // A 16 px checkbox whose big label is visibility: hidden has no 24×24 target.
  "small-checkbox-hidden-label": [{
    extra: '    <input type="checkbox" id="cb" aria-label="รับข่าวสาร" style="width: 16px; height: 16px"> <label for="cb" style="display: inline-block; min-width: 120px; min-height: 30px; visibility: hidden">รับข่าวสาร</label>',
  }, "target-size"],
  // A Thai placeholder whose ::placeholder rule sets negative tracking.
  "thai-tracking-placeholder": [{
    extra: '    <input aria-label="ค้นหา" placeholder="ค้นหาหนังสือ" style="min-height: 44px">',
    style: "    input { font: inherit; }\n    input::placeholder { letter-spacing: -0.05em; }",
  }, "thai-typography"],
  // A slow scripted drift: under a pixel per sample, still continuous motion.
  "motion-scripted-drift": [{ script: "const h = document.querySelector('main h1'); h.style.position = 'relative'; const t0 = performance.now(); (function tick(t) { h.style.left = ((t - t0) / 1000).toFixed(3) + 'px'; requestAnimationFrame(tick); })(t0);" }, "reduced-motion"],
  // A 200 ms slide started during the script-motion sample: within the limit.
  "good-css-slide-200": [{
    style: "    @keyframes nudge { from { transform: translateX(6px); } to { transform: none; } }\n    main h1.go { animation: nudge 200ms linear; }",
    script: "const h = document.querySelector('main h1'); setInterval(() => { h.classList.remove('go'); void h.offsetWidth; h.classList.add('go'); }, 2000); setTimeout(() => h.classList.add('go'), 1200);",
  }, null],
  // A long finite fade does not hide a requestAnimationFrame slide on the same element.
  "motion-scripted-with-fade": [{
    style: "    @keyframes fade { from { opacity: 0.6; } to { opacity: 1; } }\n    main h1 { animation: fade 20s linear; }",
    script: "const h = document.querySelector('main h1'); const t0 = performance.now(); (function tick(t) { h.style.transform = 'translateX(' + Math.round(10 * Math.sin((t - t0) / 150)) + 'px)'; requestAnimationFrame(tick); })(t0);",
  }, "reduced-motion"],
  // A finite fade that also repeats one transform hides nothing: the slide from script is still seen.
  "motion-scripted-constant-transform": [{
    style: "    @keyframes fadeT { from { opacity: 0.6; transform: translateY(0); } to { opacity: 1; transform: translateY(0); } }\n    main h1 { animation: fadeT 20s linear; }",
    script: "const h = document.querySelector('main h1'); const t0 = performance.now(); (function tick(t) { h.style.translate = Math.round(10 * Math.sin((t - t0) / 150)) + 'px 0'; requestAnimationFrame(tick); })(t0);",
  }, "reduced-motion"],
  // A permitted 200 ms slide on <main> moves its children too: neither is script motion.
  "good-parent-slide-200": [{
    style: "    @keyframes nudge { from { transform: translateY(6px); } to { transform: none; } }\n    main.go { animation: nudge 200ms linear; }",
    script: "const m = document.querySelector('main'); setInterval(() => { m.classList.remove('go'); void m.offsetWidth; m.classList.add('go'); }, 400);",
  }, null],
  // A permitted slide on <main> does not hide a requestAnimationFrame loop on a child.
  "motion-scripted-under-slide": [{
    style: "    @keyframes nudge { from { transform: translateY(6px); } to { transform: none; } }\n    main.go { animation: nudge 200ms linear; }",
    script: "const m = document.querySelector('main'); setInterval(() => { m.classList.remove('go'); void m.offsetWidth; m.classList.add('go'); }, 300); const h = document.querySelector('main h1'); const t0 = performance.now(); (function tick(t) { h.style.transform = 'translateY(' + Math.round(6 * Math.sin((t - t0) / 150)) + 'px)'; requestAnimationFrame(tick); })(t0);",
  }, "reduced-motion"],
  // A permitted 200 ms scale pulse on <main> changes its children's boxes: not their own motion.
  "good-parent-scale-200": [{
    style: "    @keyframes pop { from { transform: scale(0.97); } to { transform: none; } }\n    main.go { animation: pop 200ms linear; }",
    script: "const m = document.querySelector('main'); setInterval(() => { m.classList.remove('go'); void m.offsetWidth; m.classList.add('go'); }, 250);",
  }, null],
  // Short scripted moves (100 ms) every 600 ms: each is within the limit, so they are not added up.
  "good-scripted-short-moves": [{ script: "const h = document.querySelector('main h1'); setInterval(() => { const t0 = performance.now(); (function tick(t) { const k = Math.min(1, (t - t0) / 100); h.style.transform = 'translateX(' + (4 * k).toFixed(2) + 'px)'; if (k < 1) requestAnimationFrame(tick); else h.style.transform = 'none'; })(t0); }, 600);" }, null],
  // Thai in a textarea's placeholder, drawn by a system fallback.
  "thai-font-textarea-placeholder": [{
    extra: '    <textarea aria-label="ข้อความ" placeholder="พิมพ์ข้อความภาษาไทย" style="font-family: monospace; min-height: 44px"></textarea>',
  }, "thai-font"],
  // A short scripted slide is retimed to loop forever after it was first recorded.
  "motion-retimed": [{ script: "const a = document.querySelector('main h1').animate([{ transform: 'translateY(4px)' }, { transform: 'none' }], { duration: 100 }); setTimeout(() => a.effect.updateTiming({ iterations: Infinity }), 20);" }, "reduced-motion"],
  // A slide started by IntersectionObserver below the fold still ignores reduced motion.
  "motion-below-fold": [{
    style: "    .late.go { animation: rise 600ms ease-out 1 both; }\n    @keyframes rise { from { transform: translateY(24px); } to { transform: none; } }",
    extra: '    <div style="height: 2400px"></div>\n    <p class="late">ข้อความที่เลื่อนขึ้นเมื่อเลื่อนมาถึง</p>',
    script: "new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && e.target.classList.add('go'))).observe(document.querySelector('.late'));",
  }, "reduced-motion"],
  // A control faded to opacity 0 hides its outline with it.
  "focus-transparent-control": [{ extra: '    <button type="button" style="opacity: 0">ปุ่มที่มองไม่เห็น</button>' }, "focus-visible"],
  // A sentence wrapped in a span next to the link still makes the link inline.
  "good-link-in-wrapped-prose": [{
    style: "    .fine a { font-size: 11px; line-height: 1; }",
    extra: '    <p class="fine"><a href="#about">เงื่อนไข</a> <span>ใช้กับทุกคน อ่าน<a href="#books">รายละเอียด</a>เพิ่มเติม</span></p>',
  }, null],
  // A native checkbox is small, but its label is the target.
  "good-labeled-checkbox": [{ extra: '    <label style="display: flex; align-items: center; gap: 8px; min-height: 44px"><input type="checkbox"> รับข่าวสารจากร้าน</label>' }, null],
  // A field that holds Tab but lets Escape out (an editor's tab-to-indent mode) is not a trap.
  "good-tab-held-escape": [{
    extra: '    <textarea aria-label="แก้ไขโค้ด" id="ed" style="min-height:44px"></textarea>',
    script: "const ed = document.getElementById('ed'); ed.addEventListener('keydown', (e) => { if (e.key === 'Tab' && !ed.dataset.out) e.preventDefault(); if (e.key === 'Escape') ed.dataset.out = '1'; });",
  }, null],
  // Ten 150 ms slides are 1.5 s of motion under reduced motion.
  "motion-repeated-short": [{ style: "    main h1 { animation: nudge 150ms linear 10; }\n    @keyframes nudge { from { transform: translateX(4px); } to { transform: none; } }" }, "reduced-motion"],
  // A slide started from script with element.animate() is over before the probe reads the page.
  "motion-script-short": [{ script: "document.querySelector('main h1').animate([{ transform: 'translateY(16px)' }, { transform: 'none' }], { duration: 250 });" }, "reduced-motion"],
  // Thai text directly in <body> is checked too (here with negative tracking only on body).
  "thai-tracking-body": [{
    style: "    body:not(#zz) { letter-spacing: -0.03em; }\n    body:not(#zz) > * { letter-spacing: normal; }",
    script: "document.body.append(document.createTextNode('ข้อความไทยที่อยู่ใน body โดยตรง'));",
  }, "thai-typography"],
  // Noto loaded as its Latin subset only: the Thai is drawn by a system fallback.
  "thai-latin-subset-only": [{
    useFont: false,
    style: `    @font-face { font-family: "Noto Sans Thai"; font-weight: 400; src: url("${FONT}/noto-sans-thai-latin-400-normal.woff2") format("woff2"); unicode-range: U+0000-00FF; }\n    body, h1, h2, button { font-family: "Noto Sans Thai", sans-serif; }`,
  }, "thai-font"],
  // Noto Sans Thai self-hosted under a CSS alias (as next/font/local does): what renders is Noto.
  "good-noto-alias": [{
    useFont: false,
    style: `    @font-face { font-family: "brand-thai"; font-weight: 400; src: url("${FONT}/noto-sans-thai-thai-400-normal.woff2") format("woff2"); unicode-range: U+0E01-0E5B, U+200C-200D, U+25CC; }\n    @font-face { font-family: "brand-thai"; font-weight: 700; src: url("${FONT}/noto-sans-thai-thai-700-normal.woff2") format("woff2"); unicode-range: U+0E01-0E5B, U+200C-200D, U+25CC; }\n    @font-face { font-family: "brand-thai"; font-weight: 400; src: url("${FONT}/noto-sans-thai-latin-400-normal.woff2") format("woff2"); unicode-range: U+0000-00FF; }\n    body, h1, h2, button { font-family: "brand-thai", sans-serif; }`,
  }, null],
  // A Latin font first in the stack draws the spaces and Latin; only the Thai glyphs must be Noto.
  "good-latin-font-first": [{ style: '    body, h1, h2, button { font-family: "DejaVu Sans", "Noto Sans Thai", sans-serif; }' }, null],
  // A page rule for nested spans must not change the font the probe reports for its parent.
  "good-span-font-rule": [{ style: '    main p > span { font-family: monospace !important; }' }, null],
  // Tab cycles through the skip link and the header links and never reaches the page content.
  "keyboard-trap-header-cycle": [{
    script: "const navLinks = document.querySelectorAll('header nav a'); navLinks[navLinks.length - 1].addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); document.querySelector('.skip').focus(); } });",
  }, "keyboard-trap"],
  // A 500 ms fade that also repeats one transform at both ends moves nothing.
  "good-fade-constant-transform": [{ style: "    main h1 { animation: fadeonly 500ms ease-out 1 both; }\n    @keyframes fadeonly { from { opacity: 0.4; transform: translateZ(0); } to { opacity: 1; transform: translateZ(0); } }" }, null],
  // One keyframe from script moves from the element's own position (an implicit start): motion.
  "motion-single-keyframe": [{ script: "document.querySelector('main h1').animate([{ transform: 'translateX(40px)' }], { duration: 500 });" }, "reduced-motion"],
  // A one-line parent with tight leading holds a block that wraps with its own good leading.
  "good-thai-parent-with-block": [{ extra: '    <div style="line-height: 1.2">หัวข้อสั้น<p style="line-height: 1.8">ข้อความภาษาไทยในย่อหน้าลูกที่ยาวพอจะขึ้นบรรทัดใหม่ในจอแคบ โดยตั้งระยะบรรทัดของตัวเองไว้กว้างพอสำหรับสระและวรรณยุกต์</p></div>' }, null],
  // A requestAnimationFrame loop moves an element with no CSS or Web Animation behind it.
  "motion-scripted-raf": [{ script: "const h = document.querySelector('main h1'); const t0 = performance.now(); (function tick(t) { h.style.transform = 'translateX(' + Math.round(10 * Math.sin((t - t0) / 150)) + 'px)'; requestAnimationFrame(tick); })(t0);" }, "reduced-motion"],
  // An iframe holds five links; Tab walks them and comes back out to the page.
  "good-iframe-tab": [{ extra: '    <iframe title="ตัวอย่างฝัง" style="width: 100%; height: 120px; border: 0" srcdoc="<!doctype html><html lang=th><body><a href=\'#a\'>ลิงก์หนึ่ง</a> <a href=\'#b\'>ลิงก์สอง</a> <a href=\'#c\'>ลิงก์สาม</a> <a href=\'#d\'>ลิงก์สี่</a> <a href=\'#e\'>ลิงก์ห้า</a></body></html>"></iframe>' }, null],
  // Low-contrast text revealed on scroll, on a page that scrolls smoothly: the reveal must still run.
  "low-contrast-smooth-reveal": [{
    style: "    html { scroll-behavior: smooth; }\n    .late { opacity: 0; color: #a8a8a8; }\n    .late.in { opacity: 1; }",
    extra: '    <div style="height: 3200px"></div>\n    <p class="late">ข้อความสีจางที่ปรากฏเมื่อเลื่อนมาถึง</p>',
    script: "new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && e.target.classList.add('in'))).observe(document.querySelector('.late'));",
  }, "contrast"],
  // Thai text inside a display:none container (an inactive translation or closed dialog) is not rendered.
  "good-hidden-thai": [{ extra: '<div class="closed" style="display: none"><p style="letter-spacing: -0.05em">ข้อความแปลที่ยังไม่แสดง</p></div>' }, null],
};

export async function build() {
  const dir = path.join(ROOT, "fixtures");
  await rm(dir, { recursive: true, force: true });
  for (const [name, [opts]] of Object.entries(FIXTURES)) {
    await mkdir(path.join(dir, name), { recursive: true });
    await writeFile(path.join(dir, name, "index.html"), page(opts));
    await writeFile(path.join(dir, name, "design-floor.json"), JSON.stringify({
      floor: "1.0.0",
      public: true,
      target: { static: "../.." },
      routes: [`/fixtures/${name}/`],
      widths: [320, 375, 390, 768, 1440],
      waivers: [],
    }, null, 2) + "\n");
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  await build();
  console.log(`wrote ${Object.keys(FIXTURES).length} fixtures`);
}
