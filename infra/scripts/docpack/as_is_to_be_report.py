#!/usr/bin/env python3
"""Build the cross-repository As-Is / To-Be report.

Reads `docs/diagrams/01-business-process.md` from every clone under the Book Dev
GitHub root (the layout is the system-docpack skill's
`references/as-is-to-be.md`), and writes:

- `as-is-to-be.json`: one record per repository (diagrams, gaps, freshness) for agents;
- `index.html`: the visual report (As-Is and To-Be side by side, gap progress).

Read-only on every clone. Never reads files other than the one above.

usage: python3 as_is_to_be_report.py [--root <dir>] [--out <dir>] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import PROJECT_ROOT, book_dev_github_root  # noqa: E402

DEFAULT_ROOT = book_dev_github_root()
DEFAULT_OUT = PROJECT_ROOT / "data" / "generated" / "as-is-to-be"
DOC = Path("docs") / "diagrams" / "01-business-process.md"
SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".venv", "venv", "__pycache__", "vendor", ".turbo", "coverage"}
STATUSES = ("done", "doing", "todo", "blocked", "decide")
NO_TARGET = "ไม่มี To-Be ที่เขียนไว้"
STALE_DAYS = 30
# Problems that make the Gap table itself unreadable; the bar judges only these.
TABLE_PROBLEMS = ("Gap ", "no Gap", "duplicate Gap", "G0 ", "no To-Be and no decide gap")

REVIEWED_RE = re.compile(r"^Last reviewed:\s*(\d{4}-\d{2}-\d{2})(?:.*?commit\s+`([0-9a-f]{6,40})`)?", re.M)
MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)


def find_repos(root: Path) -> list[Path]:
    """Every directory under root that holds a `.git` entry; does not descend into a repository.

    A root that is itself a checkout (the hub, or one worktree) is still searched for clones below it;
    only when none are found is the root reported as the single repository.
    """
    repos: list[Path] = []
    for current, dirs, _files in os.walk(root):
        here = Path(current)
        if (here / ".git").exists() and here != root:
            repos.append(here)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
    if not repos and (root / ".git").exists():
        return [root]
    return sorted(repos)


REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def clean_git_env() -> dict[str, str]:
    """The caller's environment without GIT_* variables (a hook's GIT_DIR would point every clone at one repo)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git_common_dir(repo: Path) -> str:
    """The git directory a clone shares with its linked worktrees; the checkout path when git cannot say."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10, env=clean_git_env(), check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return str(Path(out).resolve()) if out else str(repo.resolve())


def origin_name(repo: Path) -> str | None:
    """owner/name of a GitHub origin remote, or None when there is none."""
    try:
        url = subprocess.run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True, timeout=10, env=clean_git_env()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    match = REMOTE_RE.search(url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def repo_name(repo: Path, root: Path) -> str:
    """owner/name from the origin remote; the folder layout is only a fallback (worktree names differ)."""
    named = origin_name(repo)
    if named:
        return named
    rel = repo.relative_to(root).parts
    owner = rel[0] if len(rel) > 1 else repo.parent.name
    return f"{owner}/{repo.name}"


def sections(text: str) -> list[tuple[str, str]]:
    """Split on level-2 headings into (heading, body) pairs."""
    parts = re.split(r"^## +(.+?)\s*$", text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def first_mermaid(body: str) -> str | None:
    match = MERMAID_RE.search(body)
    code = match.group(1).strip() if match else ""
    return code or None  # an empty block is not a diagram


GAP_HEADER = ["id", "as-is → to-be", "status", "owner", "evidence"]


def parse_gaps(body: str) -> list[dict] | None:
    """Rows of the first table whose header is the contract's; None when no such table."""
    gaps: list[dict] = []
    in_table = False
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            # Outer pipes are optional in Markdown: inside the table a line with a pipe is still a row.
            if in_table is True and line.strip() and "|" in line:
                pass
            elif in_table:
                break
            else:
                continue
        if not in_table:
            head = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            if head == GAP_HEADER:
                in_table = "header"
            continue
        if in_table == "header":  # the next line must be the separator row
            sep = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(sep) != 5 or not all(re.fullmatch(r":?-+:?", c) for c in sep):
                return None
            in_table = True
            continue
        row = line.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]
        if len(cells) != 5 or not re.fullmatch(r"G\d+", cells[0]):
            why = "columns" if len(cells) != 5 else "id"
            gaps.append({"id": "?", "change": line.strip(), "status": "malformed", "owner": "", "evidence": "", "why": why})
            continue
        status = cells[2].lower()
        evidence = cells[4]
        written = status
        if status not in STATUSES:
            status = "todo"  # an unknown status never counts as progress
        if status == "done" and not evidence:
            status = "todo"
        gaps.append({"id": cells[0], "change": cells[1], "status": status, "owner": cells[3], "evidence": evidence,
                     "written": written})
    return gaps if in_table is True else None


def intro(text: str) -> str:
    body = re.split(r"^## ", text, maxsplit=1, flags=re.M)[0]
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if para and not para.startswith("#") and not para.startswith("Last reviewed:"):
            return " ".join(para.split())
    return ""


def section_text(body: str) -> str:
    """The section's prose and list items (no code blocks, no Sources line), line breaks kept."""
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    keep = []
    for line in body.splitlines():
        line = line.rstrip()
        if line.startswith(("Sources:", "#")):
            continue
        keep.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def parse_doc(text: str, today: dt.date) -> dict:
    as_is = to_be = None
    to_be_note = ""
    seen_as_is = seen_to_be = False
    gaps: list[dict] | None = None
    for heading, body in sections(text):
        low = heading.lower()
        if low.startswith("as-is") and not seen_as_is:
            seen_as_is, as_is = True, first_mermaid(body)
        elif low.startswith("to-be") and not seen_to_be:
            seen_to_be, to_be = True, first_mermaid(body)
            if to_be is None:
                to_be_note = section_text(body)
        elif low == "gap" and gaps is None:
            gaps = parse_gaps(body)
    reviewed = REVIEWED_RE.search(text)
    reviewed_on = reviewed.group(1) if reviewed else None
    problems = []
    bad = [g for g in gaps or [] if g["status"] == "malformed"]
    if any(g["why"] == "columns" for g in bad):
        problems.append("Gap table has rows that do not match the five columns")
    if any(g["why"] == "id" for g in bad):
        problems.append("Gap table has rows whose ID is not G<number>")
    gaps = [g for g in gaps if g["status"] != "malformed"] if gaps is not None else None
    age = None
    if reviewed_on is not None:
        try:
            age = (today - dt.date.fromisoformat(reviewed_on)).days
        except ValueError:
            problems.append(f"invalid Last reviewed date {reviewed_on}")
            reviewed_on = None
        if age is not None and age < 0:
            problems.append(f"Last reviewed date {reviewed_on} is in the future")
    else:  # no line at all (an invalid date is reported above)
        problems.append("no Last reviewed line")
    if reviewed is not None and reviewed_on is not None and not reviewed.group(2):
        problems.append("Last reviewed line has no commit")
    review_line = text[reviewed.start():text.find("\n", reviewed.start())] if reviewed else ""
    if "[ต้องยืนยัน]" in review_line:
        problems.append("As-Is not checked against the latest code ([ต้องยืนยัน] on the Last reviewed line)")
    if age is not None and age > STALE_DAYS:
        problems.append(f"stale: reviewed {age} days ago")
    if not intro(text):
        problems.append("no summary paragraph")
    if as_is is None:
        problems.append("no As-Is diagram")
    if not seen_to_be:
        problems.append("no To-Be section")
    elif to_be is None and not to_be_note:
        problems.append("To-Be section is empty")
    if gaps is None:
        problems.append("no Gap table")
    elif not gaps:
        problems.append("Gap table has no rows")
    unknown = sorted({g["written"] for g in gaps or [] if g["written"] not in STATUSES})
    if unknown:
        problems.append("Gap status not one of " + "|".join(STATUSES) + ": " + ", ".join(unknown))
    ids = [g["id"] for g in gaps or []]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append("duplicate Gap IDs: " + ", ".join(dupes))
    # Only the contract phrase marks "no target"; any other written text To-Be is a target.
    # The phrase counts only when it opens the section; inside a written target it is a partial exception.
    # The phrase must open the section; Markdown emphasis/quote/list marks before it do not count as text.
    opening = re.sub(r"^(?:\s+|[*_>~`#]+|[-+]\s|\d+[.)]\s)+", "", to_be_note or "")
    no_target = to_be is None and (not to_be_note or opening.startswith(NO_TARGET))
    if no_target and not any(g["status"] == "decide" for g in gaps or []):
        problems.append("no To-Be and no decide gap asking the owner for one")
    for g in gaps or []:
        if g["id"] == "G0" and (g["status"] != "done" or not g["evidence"]):
            problems.append("G0 (no gap) must be done with evidence")
    if any(g["id"] == "G0" for g in gaps or []) and len(gaps) > 1:
        problems.append("G0 (no gap) must be the only row")
    # G0 = "no gap" marker row; a duplicated ID is ambiguous, so it stays out of the totals until fixed.
    for g in gaps or []:
        g["counted"] = g["id"] != "G0" and g["id"] not in dupes
    real_gaps = [g for g in gaps or [] if g["counted"]]
    counts = {s: sum(1 for g in real_gaps if g["status"] == s) for s in STATUSES}
    return {
        "summary": intro(text),
        "reviewed_on": reviewed_on,
        "reviewed_commit": reviewed.group(2) if reviewed else None,
        "age_days": age,
        "as_is": as_is,
        "to_be": to_be,
        "to_be_note": to_be_note,
        "gaps": gaps or [],
        "gap_counts": counts,
        "standard": not problems,
        "table_ok": not any(p.startswith(TABLE_PROBLEMS) for p in problems),
        "problems": problems,
    }


def collect(root: Path, today: dt.date) -> dict:
    root = root.resolve()
    systems, missing = [], []
    # One GitHub repository counts once: its clone, linked worktrees and any spare clone. Keyed by the
    # origin's owner/name (GitHub names are case-insensitive); a checkout without a GitHub origin is keyed by
    # its shared git directory, so two different repositories with the same fallback name both stay.
    # The clone wins over a worktree (its `.git` is a folder; a worktree's is a file), else the first by path.
    # A worktree given alone as --root still counts.
    # Two passes: checkouts sharing a git directory fold first (a worktree may set its own origin alias),
    # then the survivors fold by GitHub origin.
    def better(repo: Path, current: Path | None) -> bool:
        return current is None or ((repo / ".git").is_dir() and not (current / ".git").is_dir())

    by_gitdir: dict[str, Path] = {}
    for repo in find_repos(root):
        key = git_common_dir(repo)
        if better(repo, by_gitdir.get(key)):
            by_gitdir[key] = repo
    chosen: dict[str, tuple[str, Path]] = {}
    for gitdir, repo in sorted(by_gitdir.items(), key=lambda item: str(item[1])):
        named = origin_name(repo)
        key = f"origin:{named.lower()}" if named else f"gitdir:{gitdir}"
        if key not in chosen or better(repo, chosen[key][1]):
            chosen[key] = (repo_name(repo, root), repo)
    for name, repo in sorted(chosen.values(), key=lambda item: str(item[1])):
        record = {"repo": name, "path": str(repo.relative_to(root)) if repo != root else repo.name}
        doc = repo / DOC
        # Only a regular file inside the clone: no symlink anywhere from the clone down to the file.
        chain = [repo / "docs", repo / "docs" / "diagrams", doc]
        try:
            readable = doc.is_file() and not any(p.is_symlink() for p in chain) and doc.resolve().is_relative_to(repo.resolve())
            text = doc.read_text(encoding="utf-8", errors="replace") if readable else None
        except OSError as error:
            systems.append({**record, **parse_doc("", today), "problems": [f"unreadable: {error.strerror}"], "standard": False})
            continue
        if text is not None:
            systems.append({**record, **parse_doc(text, today)})
        else:
            missing.append(record["repo"])
    systems.sort(key=lambda s: (not s["standard"], s["repo"]))
    totals = {s: sum(x["gap_counts"][s] for x in systems) for s in STATUSES}
    waiting_owner = sum(
        1 for x in systems for g in x["gaps"]
        if g.get("counted") and (g["status"] == "decide" or (g["status"] == "blocked" and g["owner"].lower() == "owner"))
    )
    return {
        "generated_on": today.isoformat(),
        "stale_after_days": STALE_DAYS,
        "repositories": len(systems) + len(missing),
        "with_doc": len(systems),
        "standard": sum(1 for s in systems if s["standard"]),
        "gap_totals": totals,
        "waiting_owner": waiting_owner,
        "systems": systems,
        "missing": sorted(missing),
    }


STATUS_TH = {"done": "เสร็จ", "doing": "กำลังทำ", "todo": "ยังไม่เริ่ม", "blocked": "ติดขัด", "decide": "รอตัดสินใจ"}


def e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def code_spans(value: str) -> str:
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(value))


def bar(counts: dict, gaps: list | None = None, standard: bool = True) -> str:
    total = sum(counts.values())
    if not total:
        if not gaps:
            label = "ยังไม่มีตาราง Gap"
        elif standard and [g["id"] for g in gaps] == ["G0"]:
            label = "ไม่มี Gap (As-Is ตรงกับ To-Be)"
        else:
            label = "ยังนับ Gap ไม่ได้: แก้ตารางก่อน"
        return f'<div class="bar empty"><span>{label}</span></div>'
    segs = "".join(
        f'<i class="s-{s}" style="flex:{counts[s]}" title="{STATUS_TH[s]} {counts[s]}"></i>' for s in STATUSES if counts[s]
    )
    return f'<div class="bar" role="img" aria-label="Gap {counts["done"]} จาก {total} เสร็จ">{segs}</div>'


def diagram(code: str | None, empty: str, note: bool = False) -> str:
    if not code:
        return f'<p class="{"note" if note else "none"}">{code_spans(empty) if note else e(empty)}</p>'
    return f'<div class="dia"><pre class="mermaid">{e(code)}</pre></div>'


def render_system(s: dict) -> str:
    counts = s["gap_counts"]
    total = sum(counts.values())
    if s["reviewed_on"] is None:
        fresh = '<span class="pill warn">ไม่มีวันที่ทบทวน</span>'
    elif s["age_days"] > STALE_DAYS:
        fresh = f'<span class="pill warn">ทบทวน {e(s["reviewed_on"])} · เก่า {s["age_days"]} วัน</span>'
    else:
        fresh = f'<span class="pill ok">ทบทวน {e(s["reviewed_on"])}</span>'
    std = "" if s["standard"] else '<span class="pill off">ยังไม่เข้ามาตรฐาน</span>'
    rows = "".join(
        f'<tr><td class="mono">{e(g["id"])}</td><td>{code_spans(g["change"])}</td>'
        f'<td><span class="chip s-{g["status"]}">{STATUS_TH[g["status"]]}</span></td>'
        f'<td class="mono">{e(g["owner"])}</td><td class="ev">{code_spans(g["evidence"])}</td></tr>'
        for g in s["gaps"]
    )
    table = (
        f'<div class="tw"><table><thead><tr><th>ID</th><th>As-Is → To-Be</th><th>สถานะ</th><th>เจ้าของ</th><th>หลักฐาน</th></tr></thead><tbody>{rows}</tbody></table></div>'
        if rows else ""
    )
    problems = "".join(f"<li>{e(p)}</li>" for p in s["problems"] if not p.startswith("stale"))
    done = f'{counts["done"]}/{total} เสร็จ' if total else ""
    slug = re.sub(r"[^a-z0-9-]", "-", s["repo"].split("/")[-1].lower())
    open_attr = " open" if s["standard"] else ""
    return f"""
<details class="sys" id="{slug}"{open_attr}>
  <summary>
    <div class="sh"><h2>{e(s["repo"].split("/")[-1])}</h2><span class="owner mono">{e(s["repo"])}</span></div>
    <div class="meta">{fresh}{std}<span class="done mono">{done}</span></div>
    {bar(counts, s["gaps"], s.get("table_ok", s["standard"]))}
  </summary>
  <p class="intro">{code_spans(s["summary"])}</p>
  {f'<ul class="probs">{problems}</ul>' if problems else ''}
  <div class="pair">
    <section><h3>As-Is <small>วันนี้ จากโค้ด</small></h3>{diagram(s["as_is"], "ไม่มีภาพ As-Is")}</section>
    <section><h3>To-Be <small>เป้าหมายที่เขียนไว้</small></h3>{diagram(s["to_be"], s.get("to_be_note") or "ไม่มี To-Be ที่เขียนไว้", note=bool(s.get("to_be_note")))}</section>
  </div>
  {table}
  <p class="src mono">{e(s["path"])}/docs/diagrams/01-business-process.md{f' · commit {e(s["reviewed_commit"])}' if s["reviewed_commit"] else ''}</p>
</details>"""


CSS = """
:root{--bg:#f4f6f9;--panel:#ffffff;--fg:#17202b;--muted:#5b6675;--line:#d9dee6;--accent:#2f5bd3;
--done:#26774b;--chip-fg:#ffffff;--doing:#2f5bd3;--todo:#9aa4b2;--blocked:#c8463d;--decide:#c58a12;--decide-text:#8a5a00;
--sans:"IBM Plex Sans Thai","Noto Sans Thai",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11161d;--panel:#18202a;--fg:#e6ebf2;--muted:#9aa6b6;--line:#2a3442;--accent:#7f9ef0;
--done:#4fb37f;--doing:#7f9ef0;--todo:#8a95a5;--blocked:#e2665c;--decide:#e0a93a;--decide-text:#e0a93a;--chip-fg:#10151c;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11161d;--panel:#18202a;--fg:#e6ebf2;--muted:#9aa6b6;--line:#2a3442;--accent:#7f9ef0;
--done:#4fb37f;--doing:#7f9ef0;--todo:#8a95a5;--blocked:#e2665c;--decide:#e0a93a;--decide-text:#e0a93a;--chip-fg:#10151c;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:15px;line-height:1.6}
.wrap{max-width:1240px;margin:0 auto;padding-block:28px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:18px}
header{display:grid;gap:6px}
h1{font-size:1.7rem;margin:0;text-wrap:balance;letter-spacing:-.01em}
.lede{color:var(--muted);margin:0;max-width:70ch}
.mono,code{font-family:var(--mono);font-size:.86em}
code{background:color-mix(in srgb,var(--accent) 9%,transparent);padding:0 .3em;border-radius:3px}
.kpis{display:flex;flex-wrap:wrap;gap:10px 28px;padding:14px 0;border-block:1px solid var(--line)}
.kpi{display:grid}.kpi b{font-size:1.5rem;font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:.85rem}
.legend{display:flex;flex-wrap:wrap;gap:8px}
.bar{display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--line);gap:2px}
.bar i{display:block}.bar.empty{height:auto;background:none;color:var(--muted);font-size:.8rem}
.chip.s-todo,.chip.s-decide{color:#16202b}
.s-done{background:var(--done)}.s-doing{background:var(--doing)}.s-todo{background:var(--todo)}.s-blocked{background:var(--blocked)}.s-decide{background:var(--decide)}
.chip{display:inline-block;color:var(--chip-fg);font-weight:500;font-size:.78rem;padding:1px 8px;border-radius:999px;white-space:nowrap}
.sys{background:var(--panel);border:1px solid var(--line);border-radius:10px}
.sys>summary{list-style:none;cursor:pointer;padding:14px 18px;display:grid;gap:8px}
.sys>summary::-webkit-details-marker{display:none}
.sys>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:10px}
.sys[open]>summary{border-bottom:1px solid var(--line)}
.sh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px}
h2{font-size:1.15rem;margin:0}.owner{color:var(--muted)}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pill{font-size:.78rem;padding:1px 8px;border-radius:999px;border:1px solid var(--line)}
.pill.ok{color:var(--done);border-color:currentColor}.pill.warn{color:var(--decide-text);border-color:currentColor}.pill.off{color:var(--blocked);border-color:currentColor}
.done{color:var(--muted)}
.sys>*:not(summary){margin-left:18px;margin-right:18px}
.intro{color:var(--muted);max-width:90ch}
.probs{color:var(--blocked);font-size:.88rem;margin-block:0}
.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:14px}
.pair section{min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
h3{margin:0 0 6px;font-size:.95rem;letter-spacing:.02em}h3 small{color:var(--muted);font-weight:400;margin-left:6px}
.dia{overflow-x:auto}.dia pre{margin:0;background:none}
.none{color:var(--muted);font-style:italic}.note{color:var(--muted);max-width:70ch;white-space:pre-line}
.tw{overflow-x:auto;margin-top:14px}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:.8rem;letter-spacing:.03em}
td.ev{color:var(--muted);min-width:16ch}
.src{color:var(--muted);margin-block:12px 16px;word-break:break-all}
.miss{background:var(--panel);border:1px dashed var(--line);border-radius:10px;padding:12px 18px}
.miss summary{cursor:pointer;font-weight:500}
.miss ul{columns:3 220px;font-size:.82rem;color:var(--muted);padding-left:18px}
"""


MERMAID_FALLBACK = '''<script>
// The artifact viewer renders <pre class="mermaid"> itself. Opened as a plain file, or if the
// viewer did not draw them, load Mermaid once and draw whatever is still text.
setTimeout(function () {
  var left = Array.prototype.filter.call(document.querySelectorAll("pre.mermaid"), function (n) {
    return !n.querySelector("svg") && !n.hasAttribute("data-processed");
  });
  if (!left.length) return;
  var tag = document.createElement("script");
  tag.src = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/12.3.0/mermaid.min.js";
  tag.integrity = "sha512-CaEn6DWYJ1MM0mFAT9nvJzd3z5gPsXTM7wzprb8wx615WzTnTSrGrbKkZUqe1s24lpW27X4HlIrSFvQm/ykqZA==";
  tag.crossOrigin = "anonymous";
  tag.referrerPolicy = "no-referrer";
  tag.onload = function () {
    var dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light"
      || document.documentElement.dataset.theme === "dark";
    window.mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default", securityLevel: "strict" });
    window.mermaid.run({ nodes: left });
  };
  document.head.appendChild(tag);
}, 1500);
</script>'''


def render_html(report: dict) -> str:
    t = report["gap_totals"]
    total = sum(t.values())
    legend = "".join(f'<span class="chip s-{s}">{STATUS_TH[s]} {t[s]}</span>' for s in STATUSES)
    systems = "".join(render_system(s) for s in report["systems"])
    missing = "".join(f"<li>{e(r)}</li>" for r in report["missing"])
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>As-Is To-Be Board</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Thai:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <h1>As-Is / To-Be ทุกระบบ</h1>
  <p class="lede">ภาพระบบวันนี้ (จากโค้ด) เทียบกับเป้าหมายที่เขียนไว้ (spec, PRODUCT.md, การตัดสินใจของ owner) และ Gap ที่เหลือ สร้างจาก <code>docs/diagrams/01-business-process.md</code> ของแต่ละ repo เมื่อ {e(report["generated_on"])} ด้วย <code>task report:as-is-to-be</code></p>
</header>
<div class="kpis">
  <div class="kpi"><b>{report["standard"]}</b><span>ระบบเข้ามาตรฐาน</span></div>
  <div class="kpi"><b>{report["with_doc"]}</b><span>มีไฟล์ As-Is/To-Be</span></div>
  <div class="kpi"><b>{report["repositories"]}</b><span>repo ทั้งหมด</span></div>
  <div class="kpi"><b>{t["done"]}/{total}</b><span>Gap เสร็จแล้ว</span></div>
  <div class="kpi"><b>{report["waiting_owner"]}</b><span>รอ owner (decide หรือ blocked ที่ owner ถือ)</span></div>
</div>
<div class="legend">{legend}</div>
{systems}
<details class="miss"><summary>ยังไม่มีไฟล์ As-Is/To-Be ({len(report["missing"])} repo)</summary><ul>{missing}</ul></details>
</div>
{MERMAID_FALLBACK}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the cross-repository As-Is / To-Be report.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()
    if args.root is None or not args.root.is_dir():
        parser.error(f"root not found: {args.root} (set SOLO_BOOK_DEV_GITHUB_ROOT or --root)")
    report = collect(args.root, args.today)
    args.out.mkdir(parents=True, exist_ok=True)
    # The report gathers private repositories' docs: keep the files owner-only.
    for name, body in (("as-is-to-be.json", json.dumps(report, ensure_ascii=False, indent=1) + "\n"), ("index.html", render_html(report))):
        target = args.out / name
        # A fresh 0600 file swapped in with os.replace: a reader holding the old file never sees the new data.
        fd, tmp = tempfile.mkstemp(dir=args.out, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
            os.replace(tmp, target)
        except BaseException:
            os.unlink(tmp)
            raise
    t = report["gap_totals"]
    print(f"{report['standard']}/{report['with_doc']} standard, {report['with_doc']}/{report['repositories']} with doc, "
          f"gaps done {t['done']}/{sum(t.values())} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
