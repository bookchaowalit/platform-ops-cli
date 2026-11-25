---
title: "Design floor 1.0.0 — the quality floor every user-facing web app meets"
status: active
tags: [design-system, accessibility, wcag, thai-typography, ux, design-floor]
created: 2026-10-29
updated: 2026-11-03
description: "ADR-0007 Layer 0: the WCAG 2.2 AA quality floor with a fixed check method per rule (auto, keyboard script, manual), the rule IDs used by check.mjs and design-floor.json waivers, and which rules cannot be waived."
related: [../docs/architecture/decisions/ADR-0007-central-design-floor-and-tokens.md, README.md, CHANGELOG.md]
---

# Design floor 1.0.0

Decision: [ADR-0007](../docs/architecture/decisions/ADR-0007-central-design-floor-and-tokens.md). Conformance target: **WCAG 2.2 level AA**.

A green `check.mjs` run means the automated part of this floor passes. It is **not** a WCAG 2.2 AA conformance claim: the rows marked *manual* are signed off in the `ux-review` record before a release.

Widths for every check and screenshot: **320 / 375 / 390 / 768 / 1440** px.

## Rules

| ID | Rule (WCAG) | Method | `check.mjs` does | Waivable on public pages |
|---|---|---|---|---|
| `contrast` | Text ≥ 4.5:1, large text and UI parts ≥ 3:1 (1.4.3, 1.4.11) | auto + manual | axe `color-contrast` at 390 and 1440. Text over images, gradients and post-load theme switches is manual | no |
| `axe` | Names, roles, labels, ARIA, document structure (1.1.1, 1.3.1, 4.1.2 and others in axe's WCAG 2.x A/AA tags) | auto + manual | Every other axe violation at 390 and 1440. Whether a name is meaningful is manual | yes |
| `thai-font` | Thai text renders in Noto Sans Thai or Noto Serif Thai, self-hosted (or `next/font`) | auto | Chromium reports (DevTools `CSS.getPlatformFontsForNode`) that only Noto Sans/Serif Thai drew the Thai characters of each Thai text element (first 30), measured on a Thai-only run in the element's own font, so a Noto Latin subset over a system Thai fallback fails. The font is identified by what rendered, so a CSS alias (`next/font/local`) passes. Latin-first stacks pass | yes |
| `thai-typography` | Thai body line-height ≥ 1.6; Thai headings (`h1`–`h6` or ≥ 24 px) ≥ 1.3; no negative `letter-spacing` on Thai | auto | Computed styles of every rendered element whose own text contains Thai, where it wraps to two or more lines. An inline box counts the tallest of its own line-height, every enclosing inline box's and its block's | yes |
| `lang` | `<html lang>` set; passages in another language carry their own `lang` (3.1.1, 3.1.2) | auto + manual | `<html lang>` present; Thai text sits in a `lang="th…"` context. Latin passages inside Thai pages are manual | yes |
| `reflow` | No horizontal scroll at any floor width (1.4.10) | auto | `scrollWidth ≤ clientWidth` at all five widths | yes |
| `target-size` | Pointer targets ≥ 24×24 CSS px (2.5.8); 44×44 recommended on touch layouts | auto | Visible interactive elements at 390, except inline links inside a sentence (the WCAG inline exception: the parent has words of its own, not only other links and separators). a checkbox or radio passes when its label is 24×24 or larger; axe's `target-size` results report here too | yes |
| `skip-link` | First Tab stop is a skip link to the main content (2.4.1) | keyboard script | Presses Tab once at 1440 and checks for a same-page link whose target exists | no |
| `focus-visible` | Every focused control shows a visible indicator (2.4.7) | keyboard script + manual | Tabs through up to 40 stops at 1440 (controls inside an `<iframe>` are manual) and reads each once its transitions and scrolling have settled; each must show an `outline` (style, width, colour or offset) or `box-shadow` that it did not have before focus and that paints (a transparent outline or `0 0 #0000` shadow does not count), on a control not faded below `opacity` 0.1. Other indicator styles are reported as failures, so either add an outline/ring or waive with a reason | no |
| `focus-not-obscured` | A focused control is not entirely hidden by sticky or fixed content (2.4.11) | keyboard script | For each stop, samples five points; fails when all are covered by another element or outside the viewport. An overlay with `pointer-events: none` is invisible to the hit test, so content it hides is manual | no |
| `keyboard-trap` | Focus can always move on (2.1.2) | keyboard script | Fails when three Tabs in a row keep focus on the same element (40 when it is an `<iframe>`, whose own controls take Tabs) and Escape does not release it, or when focus returns to a stop it already visited, without having reached every tabbable control, and Escape does not break the cycle (a dialog that closes with Escape passes, and the walk carries on after it) | no |
| `reduced-motion` | With `prefers-reduced-motion: reduce`, no non-essential animation (2.3.3) | auto | Loads each route with reduced motion emulated at 390; fails on any animation or transition whose total active duration (all iterations) is over 200 ms, or infinite, including `element.animate()` ones, including ones that started and finished during load, with the page scrolled through so scroll-triggered entrances run. Script-driven motion with no animation object (a `requestAnimationFrame` loop) is caught by sampling transforms and boxes for one second; anything still moving after 200 ms fails. Under an ancestor that runs a finite animation, a child is judged by its own transform and inline geometry only; a percentage transform that follows the ancestor's animated size, or inline geometry that does not change what is drawn, can be misread (known limit, manual). Only properties whose value changes between keyframes count, and opacity, colour and visibility are not motion | yes |
| `states` | Hover, focus, disabled, loading, error and empty states exist; no redundant entry (3.3.7) | manual | — | — |
| `keyboard-operable` | Every control works from the keyboard (2.1.1) | manual | Reachability is covered by the tab walk; operation is manual | — |

Rules marked **no** cannot be waived on a page with `"public": true` in `design-floor.json` (the default).

## Reduced motion (rule M1)

`tokens.css` collapses `--duration-*` under reduced motion. Animations that do not use those variables must be switched off by the app, for example:

```css
@media (prefers-reduced-motion: reduce) {
  .hero-art { animation: none; }
}
```

Only motion that is essential to the content (a demo of the motion itself) may stay, and then it needs a waiver.

## Known limits of check.mjs (checked by hand)

`check.mjs` is frozen at 1.1.11 for heuristic edge cases: a case below fails or passes wrongly, and the `ux-review` record covers it by hand. A real false result seen on a production page reopens the checker with a fixture.

- `focus-visible`: a control whose focus ring appears only after its transition settles and that also moves focus elsewhere on focus; a fragment link whose `href` holds a literal `%` that is not an escape.
- `focus-not-obscured`: overlays with `pointer-events: none` (the hit test does not see them).
- `thai-font`: Noto Sans/Serif Thai installed on the machine running the check counts as rendered, so a page that loads no web font passes on a runner that has Noto; confirm the page serves its own font (Network panel or the font file in the build). Thai inside SVG `<text>` (the probe run is not rendered there); controls inside cross-origin frames.
- `thai-typography`: line counts of a textarea's value (it has no line boxes of its own).
- `reduced-motion`: an `element.animate()` animation that starts playing only after a later `play()`; under an animated ancestor, a child's percentage transform that follows the ancestor's size, inline geometry that changes nothing drawn, and a child whose own transform changes at the moment the ancestor's animation starts or ends; an element that drifts by script while it also restarts a short allowed CSS animation of its own (the animated samples are skipped, so the drift can go unseen); a static element pushed around by a sibling's repeated short size animation (read as script motion).

## Content policy is not part of the floor

Agency `DESIGN.md` files also forbid client claims, testimonials, pricing, credentials or lead forms without approval. Those rules stay in each `DESIGN.md`; they are content policy, not UI.
