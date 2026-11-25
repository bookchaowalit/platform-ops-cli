---
title: "Design floor — reference tokens and the automated floor check"
status: active
tags: [design-system, design-tokens, accessibility, design-floor, how-to]
created: 2026-10-29
updated: 2026-11-03
description: "How an app adopts the ADR-0007 design floor: vendor tokens.css unchanged, override values in app-theme.css, add a design-floor.json, run check.mjs; how the maintainer releases a new token version, scans for drift and reports adoption."
related: [FLOOR.md, CHANGELOG.md, ../docs/architecture/decisions/ADR-0007-central-design-floor-and-tokens.md]
---

# Design floor

Reference files for [ADR-0007](../docs/architecture/decisions/ADR-0007-central-design-floor-and-tokens.md): a shared quality floor ([FLOOR.md](FLOOR.md)) and a shared token vocabulary that every app copies in. Each app keeps its own visual world in its `DESIGN.md`.

| File | What it is |
|---|---|
| `tokens.css` | Token names with safe defaults, dark set, focus ring, Thai fallback face |
| `tailwind/v4.css` | Tailwind v4 `@theme inline` mapping and `dark:` bound to `data-theme` |
| `tailwind/v3.config.snippet.js` | The same mapping for Tailwind v3 |
| `aliases/shadcn.css` | shadcn/ui variables mapped to the base names |
| `presets/book-brand.css` | Brand values for bookchaowalit.com and BooknBook Consulting only |
| `FLOOR.md` | The rules, their WCAG references and check methods |
| `check.mjs` | Runs the automated and keyboard-script rules against an app |
| `releases.json`, `CHANGELOG.md` | Released versions and their hashes |
| `fixtures/`, `test/` | Pages that pass or fail exactly one rule, and the tests that prove it |

## Start a new app

New web apps start from the template repo [bookchaowalit/web-starter](https://github.com/bookchaowalit/web-starter) (`gh repo create bookchaowalit/<name> --template bookchaowalit/web-starter --public --clone`). It already carries every step below: vendored `tokens.css` and `tailwind/v4.css`, Thai line-heights in place of Tailwind's defaults, self-hosted Noto Thai, `design-floor.json`, the CI job, and the scrubbed Sentry setup. The steps below are for an existing app.

## Adopt in an app

1. Copy `tokens.css` (plus `tailwind/v4.css`, `aliases/shadcn.css` or the brand preset if you need them) into the app, for example `src/styles/design-floor/`. **Do not edit the copies**: the first line carries a hash, and the drift scan reports edited copies.
2. Create `app-theme.css` with your values, imported after the vendored files:

   ```css
   @import "tailwindcss";
   @import "./design-floor/tokens.css";
   @import "./design-floor/tailwind/v4.css";
   @import "./app-theme.css";
   ```

   **Tailwind v4:** import the vendored files into the base layer (`@import "./design-floor/tokens.css" layer(base);`). Unlayered CSS beats every Tailwind layer, so without `layer(base)` the default focus ring and `:lang(th)` reset would override utilities such as `focus-visible:outline-none` or `tracking-wide`.

   **shadcn/ui apps:** use `aliases/shadcn.css` and do **not** import `tailwind/v4.css`. shadcn's own `@theme inline` already defines `--color-accent`, `--color-border` and others with shadcn meanings (`bg-accent` is a hover surface), and `tailwind/v4.css` would replace them. Remove the shadcn core variables from your own unlayered `:root`/`.dark`, or they override the aliases.

3. Load Noto Sans Thai (or Noto Serif Thai): `next/font/google` in Next.js, or self-hosted `@font-face` files. Do not load it from a third-party font CDN at runtime.
4. Add a **Tokens** section to `DESIGN.md` with your values and whether the app has dark mode.
5. Add `design-floor.json` at the repo root:

   ```json
   {
     "floor": "1.0.0",
     "public": true,
     "target": { "static": "out" },
     "routes": ["/", "/projects"],
     "widths": [320, 375, 390, 768, 1440],
     "waivers": []
   }
   ```

   `target` is one of:
   - `{ "static": "out" }`: a static export, served by the checker itself;
   - `{ "serve": ["npm", "run", "start", "--", "-p", "4173"], "url": "http://localhost:4173", "timeoutSeconds": 120 }`: a command that starts a server, as an argv array run without a shell. It runs with the same trust as the repo's own `npm` scripts, so run the check only on code you would build anyway, and never with deploy secrets in the job environment;
   - `{ "url": "https://preview.example.com" }`: a deployed preview.

   A route is a path, or `{ "path": "/no-such-page/", "status": 404 }` for a page that must answer with that status. Check the not-found page this way, through a URL that does not exist, not by its own path: static hosts answer a miss with `404.html` and status 404, and the checker's static server does the same, so local and deployed runs agree. Any other route fails on a status of 400 or more.

   A waiver is `{ "rule": "reflow", "reason": "wide data table", "expires": "2027-04-30", "routes": ["/data"] }`. It must also be listed in `DESIGN.md`. Expired waivers fail. On public apps, `contrast`, `skip-link`, `focus-visible`, `focus-not-obscured` and `keyboard-trap` cannot be waived.
6. Run the check:

   ```bash
   node <path-to>/architecture-docs/design-floor/check.mjs --config design-floor.json --json design-floor-report.json --shots design-floor-shots
   ```

   Exit 0 means no unwaived failures. The report says which rows are still manual. A pass is not a WCAG conformance claim.
7. In CI (public repos), call the reusable job instead of copying the checker:

   ```yaml
     design-floor:
       uses: bookchaowalit/platform-ci-workflows/.github/workflows/design-floor.yml@<sha> # v1.5.0
       with:
         build-command: npm run build
   ```

   Add `build-env` with public flags when production renders something CI would not (for example the analytics consent banner). After each deploy, also run the check once with `target.url` set to the live site. Private repos keep the local report until the Railway runner image carries Chromium's libraries (D-9).

## Run the tests here

```bash
cd design-floor
npm ci
npx playwright install --with-deps --only-shell chromium   # CI or a machine with sudo
npm test
```

Under WSL without the system libraries, extract `libnss3`, `libnspr4` and `libasound2t64` with `apt-get download` + `dpkg-deb -x` into a local folder, then run `npm test` with `LD_LIBRARY_PATH` set to that folder's `usr/lib/x86_64-linux-gnu` and its `nss/` subfolder.

## Release a new version (maintainer)

1. Edit the reference file and bump the version in its header line (semver: added token or default = minor, rename or removal = major with an alias kept for one major).
2. `python3 scripts/design_floor_drift.py --stamp` rewrites the header hashes and records them in `releases.json`. It refuses to restamp an already released version with new content.
3. Add a `CHANGELOG.md` entry, run `npm test` here and `task check` at the repo root.
4. When `check.mjs`, `package.json` or `package-lock.json` changed, copy them unchanged to `platform-ci-workflows/design-floor/` and follow its `SOURCE.md` (vendor commit, then `FLOOR_REF` + minor release). CI keeps running the old checker until then.

## Find drift across apps

```bash
python3 scripts/design_floor_drift.py --scan ~/bookchaowalit-empire/repositories/github
```

Each vendored file is reported as `current`, `behind (latest x.y.z)`, `edited` (content no longer matches its header) or `unknown` (a version or hash not in `releases.json`). The command exits 1 if any copy is edited or unknown.

## See who adopted the floor

```bash
python3 scripts/design_floor_drift.py --adoption ~/work ~/bookchaowalit-empire/repositories   # or: task design-floor:adoption ROOT=...
```

Reports one row per git repository with a `design-floor.json` or vendored floor files: where its config is, the status of each vendored file, and which `platform-ci-workflows` release of the design-floor job its CI calls (`none` = local report only). Test fixtures and clones of `architecture-docs` itself are left out. It reads the working tree, so pull the clones first. The Big Loop scorecard (`bookchaowalit-empire/infra/scripts/platform/app_goal_scorecard.py`) shows the same adoption per web UI repository from `origin/HEAD`, as an informational `design_floor` column that never changes a level.
