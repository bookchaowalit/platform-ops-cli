---
title: "Design floor changelog"
status: active
tags: [design-system, design-tokens, changelog, design-floor]
created: 2026-10-29
updated: 2026-11-03
description: "Release history of the ADR-0007 design floor files (tokens, Tailwind v4 mapping, shadcn aliases, brand preset) and the floor rules. Semver: new token or default = minor; rename or removal = major, with the old name kept as an alias for one major version."
related: [README.md, FLOOR.md, ../docs/architecture/decisions/ADR-0007-central-design-floor-and-tokens.md]
---

# Design floor changelog

Hashes for every released file are in [releases.json](releases.json); `scripts/design_floor_drift.py --check` keeps them in step.

## check.mjs 1.1.11 — 2026-11-04

- `reduced-motion` (script motion): elements under a finite moving animation, on themselves or an ancestor, are left to the exact timing, so a permitted 200 ms slide on `<main>` does not fail its children (`good-parent-slide-200`). Under an animated ancestor, only what script motion writes is read for a child, its own transform and its inline geometry (left, top, size, margins), since every box reading moves with the ancestor's transform or animated layout, so its own script motion is still caught (`motion-scripted-under-slide`, `good-parent-scale-200`); switching between the two readings is not movement. The longest run of changes is the duration (separate short moves are not added up, `good-scripted-short-moves`), less one sample for sampling uncertainty.
- `thai-font`, `thai-typography`, `lang`: a textarea's value or, while empty, its placeholder (with `::placeholder` styles) is checked (`thai-font-textarea-placeholder`).

## check.mjs 1.1.10 — 2026-11-03

- `thai-font`, `thai-typography`, `lang`: an `<input>`'s shown text counts: its value, or its placeholder while empty (only on text-like types that draw one: text, search, url, tel, email, number; a button input's label). A shown placeholder is judged with its `::placeholder` styles, for both the font probe and letter-spacing (`thai-font-input`, `thai-tracking-placeholder`).
- `reduced-motion` (script motion): every rendered element is sampled, including ones with an allowed CSS animation such as a colour pulse (`motion-scripted-with-color`); elements that are not rendered are skipped; and motion is counted by the samples that changed, so single nudges with idle gaps between them are not continuous motion (`good-scripted-nudges`); boxes are compared to 0.01 px, so a slow sub-pixel drift is seen (`motion-scripted-drift`). While a finite CSS or Web Animation that moves something (not a fade or colour change, `motion-scripted-with-fade`) runs on an element, that element is left to the exact timing above, so a permitted 200 ms slide is not failed by the sampler (`good-css-slide-200`). The switch into or out of that state is not counted as movement, and recorded animations are read after the sample, so one that starts during it is still timed. Whether an animation moves is read as `recordMotion` reads it, so a fade that repeats one constant transform does not hide script motion (`motion-scripted-constant-transform`), and gaps of up to two samples between changes count as moving, so a tween drawn at a low frame rate is timed in full.
- `target-size`: a checkbox or radio is exempt through its label only when the label is visible (`small-checkbox-hidden-label`).
- `reduced-motion`: the script-motion sample includes each element's height, so a bar that only grows and shrinks is caught (`motion-scripted-height`).
- Serve: a URL that already answers before `serve` starts is refused (the app would fail to bind and the checker would test whatever holds the port), and an app that exits during startup fails at once with its exit code instead of at the deadline (unit tests).
- The static server's directory redirect keeps the request's own escaping, so a folder named `a#b` redirects to `/a%23b/` (unit test).

## check.mjs 1.1.9 — 2026-11-03

- `keyboard-trap`, `focus-visible`, `focus-not-obscured`: when Escape lets focus out of a dialog or a control that held Tab, the walk carries on from where focus landed (up to three times), so the controls after the dialog are still checked. Before, the walk stopped there (`focus-after-modal`).
- `thai-font`: the Thai-only probe run for a `textarea`, `select` or `option` is laid out in `<body>` in the control's language: a child of those controls is never rendered, so their Thai was skipped (`thai-font-textarea`).
- `reduced-motion`: an animation that passed when first recorded is read again at probe time, so one retimed to loop forever afterwards (`updateTiming`) is caught (`motion-retimed`).
- `design_floor_drift.py --adoption`: a commented-out `uses:` line is not counted as a CI adoption (unit test).

## check.mjs 1.1.8 — 2026-11-03

- `keyboard-trap`: while focus is inside an `<iframe>`, the outer page keeps the frame as its focus for as many Tabs as the frame has controls. The checker now allows the full 40-stop budget for focus to come back out before calling it held, so a frame with three or more links is not a trap (`good-iframe-tab`). `focus-visible` is not judged on a frame stop: the ring is drawn in the frame's own document, so frame content is a manual check.
- `reduced-motion`: motion from script that leaves no CSS or Web Animation behind (a `requestAnimationFrame` loop writing `style.transform`, libraries that set inline styles) is caught by sampling each element's transform and box for one second after the reveal pass; an element still moving after 200 ms fails (`motion-scripted-raf`).
- Known limit, now listed in FLOOR.md: `focus-not-obscured` asks the browser which element is on top at each point, and an overlay with `pointer-events: none` is not returned by that query, so a translucent banner that hides a focused control is not seen. That case stays manual.

## check.mjs 1.1.7 — 2026-11-03

- `reduced-motion`: a property counts as unchanged only when it is set at both ends (offsets 0 and 1) and every keyframe that sets it agrees. A single keyframe from script (`element.animate([{ transform: … }])`) has an implicit start and does move (`motion-single-keyframe`).
- `thai-typography`: a block's lines are counted from its own line boxes only. Text in a descendant block, which has its own leading, no longer makes a one-line parent look wrapped (`good-thai-parent-with-block`).
- The static server answers a directory requested without its trailing slash with a 301 to `dir/`, as static hosts do, so relative URLs in its index resolve as they do in production (unit test).

## check.mjs 1.1.6 — 2026-11-03

- `keyboard-trap`: focus that comes back to any stop it already visited, the first one included, is a cycle. Before, a return to the first stop was taken as a wrap, so a trap through the skip link and header was missed (`keyboard-trap-header-cycle`). A cycle that reached every tabbable control on the page is a wrap, and Escape still releases a dialog.
- `reduced-motion`: only properties whose value changes between keyframes count, so a fade that repeats one `transform` at every keyframe is not motion (`good-fade-constant-transform`).

## check.mjs 1.1.5 — 2026-11-03

- `thai-font`: the Thai-only probe run copies the element's computed font properties (family, weight, style, stretch, size, variant, feature and variation settings, synthesis, kerning) as `!important` inline styles. A page rule for nested spans, such as `p > span { font-family: … }`, could otherwise give the probe a different font from the text it stands for (`good-span-font-rule`).

## check.mjs 1.1.4 — 2026-11-03

Review of 1.1.3. No token changes.

- `thai-font`: Chromium is asked about a Thai-only run of each tagged element, laid out off screen in the element's font. Any font other than Noto Sans/Serif Thai drawing those glyphs fails. Before, a Noto Latin subset in mixed text could hide a system Thai fallback. The document-level "Noto font face loaded" check, which matched CSS family names, is gone: the font is identified by what rendered, so Noto loaded under an alias such as `next/font/local` passes (`good-noto-alias`; `thai-latin-subset-only` fails on more elements than before). The Thai pieces are joined with no separator: a space would be drawn by the first font in the stack, such as Geist, and read as a fallback (`good-latin-font-first`; found by running 1.1.4 against the pilots before release).
- The reveal pass scrolls with `behavior: "instant"`, so on a `scroll-behavior: smooth` page, scroll-triggered content is reached and checked (`low-contrast-smooth-reveal`).

## check.mjs 1.1.3 — 2026-11-03

Review of 1.1.1/1.1.2 found five gaps. No token changes; `reduced-motion`, `thai-typography` and `keyboard-trap` see more, `keyboard-trap` also passes a control that lets Escape out.

- `keyboard-trap`: before reporting a control that holds Tab, the checker presses Escape and Tab, as it already did for a focus cycle (`good-tab-held-escape`; `keyboard-trap` still fails).
- `reduced-motion`:
  - The limit applies to the total active duration, so ten 150 ms slides count as 1.5 s (`motion-repeated-short`).
  - `element.animate()` calls are recorded when they are made, so a short scripted slide is counted after it finishes (`motion-script-short`).
- `thai-typography`, `thai-font`, `lang`: Thai text directly in `<body>` is checked (`thai-tracking-body`).
- The static server compares paths segment by segment, so `/..%2fsite-secret/` no longer reads a sibling folder whose name starts with the root's name (unit test).

## check.mjs 1.1.2 — 2026-11-03

No rule or behaviour change. `check.mjs` and its tests are type-checked: JSDoc types throughout, `tsconfig.json` (strict, `checkJs`), `globals.d.ts` for the probes' `window` hooks, and `npm test` runs `tsc` first (`npm run typecheck` on its own). TypeScript and `@types/node` are dev dependencies only, so the CI job's `npm ci --omit=dev` is unchanged. Two guards made explicit: the serve process group is signalled only when the process started, and a missing response status stops the run as an error.

## check.mjs 1.1.1 — 2026-11-03

Review of 1.1.0 found false results in both directions. No token changes.

- `focus-visible`:
  - The outline's offset is part of the comparison with the resting state. A control whose `:focus-visible` only moves its outline out from the edge (`-2px` to `4px`) was flagged, and a public app cannot waive `focus-visible`. New passing fixture `good-focus-offset-ring`.
  - A control faded to `opacity` below 0.1, by itself or an ancestor, has no visible indicator (`focus-transparent-control`).
- `thai-typography`: Thai in an inline box is checked against the tallest line-height among its own, every enclosing inline box's (`display: contents` has no box and is skipped) and its block's. Before, inline boxes in between were skipped, so a tight inner span inside a tall outer span was flagged. New passing fixture `good-thai-nested-inline`.
- `keyboard-trap`: before a focus cycle is reported, the checker presses Escape and Tab. A dialog that holds focus on purpose and closes with Escape passes (`good-modal-escape`).
- `reduced-motion`: the page is scrolled through in the reduced-motion context too, so entrances that IntersectionObserver starts below the fold are recorded (`motion-below-fold`).
- `target-size`:
  - The inline exception counts the parent's words inside nested wrappers, leaving out only the text of controls (`good-link-in-wrapped-prose`).
  - A checkbox or radio whose label is 24×24 px or larger passes (`good-labeled-checkbox`).
- Serve:
  - Each readiness request is aborted at the startup deadline, so an app that accepts a connection but never answers still times out.
  - An app that exits during startup now shows its real error instead of `kill ESRCH`.

## check.mjs 1.1.0 — 2026-11-03

No token changes and no new rules, but the existing rules are stricter: an app that passed 1.0.3 can fail 1.1.0. These are the false passes found in review of the R3 job:

- `keyboard-trap`: if focus goes back to an element it already visited (other than the first Tab stop), focus is circling a group it can't leave. This fails now. Before, any repeat was taken as wrapping around the page. New fixture `keyboard-trap-cycle`.
- `reduced-motion`: CSS animations and transitions are recorded as they start (init script). One that finishes before the probe runs, such as a 250 ms slide, is still counted. New fixture `motion-short-unguarded`.
- `focus-visible`: the outline and box-shadow are compared with how the element looked before any Tab. A shadow the element always has is not a focus indicator, and neither is a transparent outline (Tailwind `outline-hidden`) or a transparent reset shadow (`0 0 #0000`). New fixtures `focus-permanent-shadow`, `focus-transparent-outline` and `good-focus-shadow-ring`. Each Tab stop is read once it settles: the element's finite transitions have finished (up to 1 s) and its position has stopped moving (smooth scrolling), so a `transition-all` ring that starts transparent and a link still scrolling into view are judged on their final state (`good-focus-ring-transition`, `good-smooth-scroll`). This was found by running 1.1.0 against the three pilots in production.
- `focus-not-obscured`: a focused element outside the viewport counts as hidden. Before, points outside the viewport counted as visible. New fixture `focus-offscreen`.
- `thai-typography`: Thai in an inline box is checked against the taller of its own line-height and its block's, because the block sets the line box. Headings include their descendants. New fixture `thai-tight-inline`.
- `target-size`: the inline exception counts only the parent's own words, not the text of sibling links, so a row of links separated by `·` no longer counts as a sentence. New fixture `small-targets-link-row`.
- axe `target-size` violations are reported under `target-size`, not `axe`, so a `target-size` waiver applies to them.
- Run hygiene: a served app is stopped even if Chromium fails to launch. The reveal scroll stops after 60 screens, so an infinite feed (fixture `good-infinite-feed`) no longer hangs the run.

## check.mjs 1.0.3 — 2026-11-02

No rule or token changes. Three false results fixed, found in review of the R3 job:

- `reduced-motion`: a colour-only animation counted as motion, because `getKeyframes()` names properties in camelCase and expands shorthands (`border-color` becomes `borderTopColor`...). Any `*-color` property now counts as colour. New passing fixture `good-color-pulse`.
- `thai-font`, `thai-typography`, `lang`: Thai text inside a `display: none` container was counted. Elements are now skipped unless `checkVisibility()` says they render, asked of the nearest ancestor with a box when they are `display: contents`; the element's own `visibility` decides the rest, so a visible child of a hidden parent is still checked. Fixtures `good-hidden-thai` (passes), `thai-tracking-contents` and `thai-tracking-visible-child` (still checked).
- Screenshots carry the route's position (`02-a_b-390.png`), so `/a-b` and `/a/b`, or two Thai paths, no longer overwrite each other.
- The in-page probes hold their patterns as regex literals instead of `new RegExp(argument)`. Semgrep's `detect-non-literal-regexp` blocked the copy vendored into `platform-ci-workflows` (ADR-0007 R3).

## check.mjs 1.0.2 — 2026-10-29

Found by running the floor check against production after the R2 merges. No token or rule changes.

- A route may be `{ "path", "status" }`, so a not-found page is checked as an expected 404. Before, a real 404 from the host stopped the run.
- The static server answers a miss with `404.html` and status 404, as Vercel, Netlify and Pages do.

## check.mjs 1.0.1 — 2026-10-29

Tuning from the R2 pilots. No token or rule changes, so no reference file version moves.

- `thai-typography` counts only boxes that are not inline and have at least two text lines. Lines are taken from text nodes, so an icon beside one line is not a second line.
- `target-size` ignores 1×1 px elements, such as an sr-only skip link before it is focused.
- `thai-font` skips elements where nothing is painted.
- Before any check, the page is scrolled through once, so `whileInView` and IntersectionObserver entrances are revealed. Finite WAAPI animations, plus opacity and transform changes driven by JavaScript (framer-motion), are allowed to settle. Without this, axe measured contrast on half-faded text.
- `reduced-motion` counts motion only: transform, position and size. Opacity and colour changes are not motion under WCAG 2.3.3.
- Six more passing fixtures: tight single-line leading, sr-only skip link, icon chip, finite fade-in, opacity pulse.
- `target.serve` must be an argv array and runs without a shell.
- The `tokens.css` header name is now `css-vars`, and the content is unchanged. Gitleaks' `generic-api-key` rule fired on the word "tokens" next to the sha256 in the header line. Keep secret-scanner keywords (token, key, secret, password, api) out of every header name.

## 1.0.0 — 2026-10-29

First release (ADR-0007 rollout step R1).

- `tokens.css` (header name `css-vars` 1.0.0): color roles with a dark set under `[data-theme="dark"]`; Thai-first font stacks with a local fallback face; type scale; `--leading-thai` 1.7 and `--leading-thai-heading` 1.35; space, radius, shadow, motion (durations collapse under reduced motion); floor sizes (`--target-min`, `--target-touch`, focus ring); default `:focus-visible` ring; `letter-spacing: normal` on `:lang(th)`.
- `tailwind/v4.css` (tailwind-v4 1.0.0): `@theme inline` mapping and `@custom-variant dark` bound to `data-theme`.
- `tailwind/v3.config.snippet.js`: the same mapping for Tailwind v3 apps.
- `aliases/shadcn.css` (aliases-shadcn 1.0.0): shadcn/ui variables read from the base names.
- `presets/book-brand.css` (preset-book-brand 1.0.0): BooknBook / bookchaowalit.com brand values; success and danger text darkened to pass 4.5:1.
- `FLOOR.md` 1.0.0: twelve automated or keyboard-script rules and two manual rows at WCAG 2.2 AA.
- `check.mjs`: the floor runner, with 17 fixtures (5 passing pages: default, dark, brand preset light and dark, shadcn aliases; 12 pages that each fail exactly one rule) and config and waiver tests.
