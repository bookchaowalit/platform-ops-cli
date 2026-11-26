#!/usr/bin/env python3
"""Tests for the cross-repository As-Is / To-Be report."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docpack"))

import as_is_to_be_report as report  # noqa: E402

TODAY = dt.date(2026, 11, 26)

STANDARD = """# 01 — Business process (As-Is / To-Be)

Service ตัวอย่าง

Last reviewed: 2026-11-20 · commit `abc1234`

## As-Is (จากโค้ดวันนี้)

```mermaid
flowchart LR
  A["<b>today</b>"] --> B
```

### As-Is: sub process

```mermaid
flowchart TD
  X --> Y
```

## To-Be (จาก spec)

```mermaid
flowchart LR
  A --> C
```

## Gap

| ID | As-Is → To-Be | Status | Owner | Evidence |
|---|---|---|---|---|
| G1 | a → b | done | claude | `specs/001/spec.md` |
| G2 | b → c | Done | claude |  |
| G3 | c → d | waiting | owner | x |
| G4 | d → e | decide | owner | owner item |
"""

LEGACY = """# 01 — Business process (As-Is / To-Be)

old style

## As-Is

```mermaid
flowchart LR
  A --> B
```

## To-Be

ไม่มี To-Be ที่เขียนไว้
"""


class ParseTest(unittest.TestCase):
    def test_standard_file(self):
        doc = report.parse_doc(STANDARD, TODAY)
        self.assertTrue(doc["standard"], doc["problems"])
        self.assertEqual(doc["reviewed_on"], "2026-11-20")
        self.assertEqual(doc["reviewed_commit"], "abc1234")
        self.assertEqual(doc["summary"], "Service ตัวอย่าง")
        self.assertIn("<b>today</b>", doc["as_is"])
        self.assertNotIn("X --> Y", doc["as_is"])  # first block only
        self.assertIn("A --> C", doc["to_be"])

    def test_gap_status_rules(self):
        gaps = {g["id"]: g["status"] for g in report.parse_doc(STANDARD, TODAY)["gaps"]}
        self.assertEqual(gaps, {"G1": "done", "G2": "todo", "G3": "todo", "G4": "decide"})

    def test_legacy_file_is_not_standard(self):
        doc = report.parse_doc(LEGACY, TODAY)
        self.assertFalse(doc["standard"])
        self.assertIn("no Gap table", doc["problems"])
        self.assertIn("no Last reviewed line", doc["problems"])
        self.assertIsNone(doc["to_be"])

    def test_stale_file(self):
        doc = report.parse_doc(STANDARD.replace("2026-11-20", "2026-09-01"), TODAY)
        self.assertFalse(doc["standard"])
        self.assertTrue(any(p.startswith("stale") for p in doc["problems"]))


class CollectTest(unittest.TestCase):
    def test_collect_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in (("alpha", STANDARD), ("beta", LEGACY), ("gamma", None)):
                repo = root / "owner" / "public" / name
                (repo / ".git").mkdir(parents=True)
                if text:
                    (repo / "docs" / "diagrams").mkdir(parents=True)
                    (repo / "docs" / "diagrams" / "01-business-process.md").write_text(text, encoding="utf-8")
                (repo / "node_modules" / "x" / ".git").mkdir(parents=True)  # never counted
            data = report.collect(root, TODAY)
            self.assertEqual(data["repositories"], 3)
            self.assertEqual(data["with_doc"], 2)
            self.assertEqual(data["standard"], 1)
            self.assertEqual(data["missing"], ["owner/gamma"])
            self.assertEqual(data["systems"][0]["repo"], "owner/alpha")
            self.assertEqual(data["gap_totals"]["done"], 1)
            page = report.render_html(data)
            self.assertTrue(page.startswith("<title>"))
            self.assertIn('<pre class="mermaid">', page)
            self.assertIn("&lt;b&gt;today&lt;/b&gt;", page)  # diagram text is escaped
            self.assertNotIn("<b>today</b>", page)


if __name__ == "__main__":
    unittest.main()
