#!/usr/bin/env python3
"""Design-floor drift regressions: stamped references, released-version guard, vendored copy status."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
import design_floor_drift as drift


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = fn(*args)
    return code, out.getvalue()


class DesignFloorDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.floor = self.root / "design-floor"
        self.floor.mkdir()
        for rel in drift.REFERENCE_FILES + ["releases.json"]:
            (self.floor / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(drift.FLOOR / rel, self.floor / rel)
        self.saved = (drift.FLOOR, drift.RELEASES)
        drift.FLOOR, drift.RELEASES = self.floor, self.floor / "releases.json"
        self.addCleanup(self.restore)

    def restore(self):
        drift.FLOOR, drift.RELEASES = self.saved

    def vendor(self, name="app", rel="tokens.css"):
        dest = self.root / "apps" / name / "src/styles" / Path(rel).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.floor / rel, dest)
        return dest

    def scan(self):
        code, out = quiet(drift.scan, [str(self.root / "apps")], True)
        return code, {Path(r["path"]).parts[-4]: r["status"] for r in json.loads(out)}

    def test_repository_references_are_stamped_and_released(self):
        self.restore()
        code, out = quiet(drift.check)
        self.assertEqual(code, 0, out)

    def test_unchanged_copy_is_current(self):
        self.vendor()
        code, status = self.scan()
        self.assertEqual((code, status), (0, {"app": "current"}))

    def test_edited_copy_is_reported_and_fails(self):
        dest = self.vendor()
        dest.write_text(dest.read_text().replace("--color-bg: #ffffff", "--color-bg: #fafafa"))
        code, status = self.scan()
        self.assertEqual((code, status), (1, {"app": "edited"}))

    def test_older_release_is_behind(self):
        self.vendor("old")
        tokens = self.floor / "tokens.css"
        tokens.write_text(tokens.read_text().replace("css-vars 1.0.0", "css-vars 1.1.0", 1).replace("--space-16: 4rem;", "--space-16: 4rem;\n  --space-24: 6rem;"))
        self.assertEqual(quiet(drift.stamp)[0], 0)
        self.vendor("new")
        code, status = self.scan()
        self.assertEqual(code, 0)
        self.assertEqual(status, {"old": "behind (latest 1.1.0)", "new": "current"})

    def test_unreleased_version_is_unknown(self):
        dest = self.vendor()
        first, body = drift.split(dest.read_text())
        dest.write_text(first.replace("1.0.0", "9.9.9") + "\n" + body)
        code, status = self.scan()
        self.assertEqual((code, status), (1, {"app": "unknown"}))

    def test_adoption_groups_config_files_and_ci_job_by_repository(self):
        app = self.root / "apps" / "app"
        (app / ".git").mkdir(parents=True)
        self.vendor()
        (app / "design-floor.json").write_text("{}")
        (app / ".github/workflows").mkdir(parents=True)
        (app / ".github/workflows/ci.yml").write_text(
            "jobs:\n  floor:\n    uses: bookchaowalit/platform-ci-workflows/.github/workflows/design-floor.yml@20c2186 # v1.5.0\n")
        bare = self.root / "apps" / "bare"
        (bare / ".git").mkdir(parents=True)
        self.vendor("bare")
        # A commented-out call is not an adoption.
        (bare / ".github/workflows").mkdir(parents=True)
        (bare / ".github/workflows/ci.yml").write_text(
            "jobs:\n  # floor:\n  #   uses: bookchaowalit/platform-ci-workflows/.github/workflows/design-floor.yml@20c2186 # v1.5.0\n")
        (self.root / "apps" / "other" / ".git").mkdir(parents=True)
        fixture = self.root / "apps" / "checker" / "tests" / "fixtures" / "x"
        fixture.mkdir(parents=True)
        (self.root / "apps" / "checker" / ".git").mkdir()
        (fixture / "design-floor.json").write_text("{}")
        source = self.root / "apps" / "source"
        (source / ".git").mkdir(parents=True)
        (source / "design-floor").mkdir()
        (source / "design-floor" / "releases.json").write_text("{}")
        (source / "design-floor.json").write_text("{}")
        rows = {Path(r["repo"]).name: r for r in drift.adoption_rows([str(self.root / "apps")])}
        self.assertEqual(set(rows), {"app", "bare"})
        self.assertEqual(rows["app"]["config"], ["design-floor.json"])
        self.assertEqual(rows["app"]["files"], ["css-vars 1.0.0 current"])
        self.assertEqual(rows["app"]["ci"], "v1.5.0")
        self.assertEqual((rows["bare"]["config"], rows["bare"]["ci"]), ([], ""))

    def test_stamp_refuses_new_content_under_a_released_version(self):
        tokens = self.floor / "tokens.css"
        tokens.write_text(tokens.read_text().replace("--radius-lg: 1rem;", "--radius-lg: 1.25rem;"))
        code, out = quiet(drift.stamp)
        self.assertEqual(code, 1)
        self.assertIn("bump the version", out)

    def test_check_flags_unstamped_edit(self):
        tokens = self.floor / "tokens.css"
        tokens.write_text(tokens.read_text().replace("--radius-lg: 1rem;", "--radius-lg: 1.25rem;"))
        code, out = quiet(drift.check)
        self.assertEqual(code, 1)
        self.assertIn("does not match content", out)


if __name__ == "__main__":
    unittest.main()
