#!/usr/bin/env python3
"""Smoke tests for the technology taxonomy and implementation graph guard."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import skip_without_knowledge  # noqa: E402

skip_without_knowledge("technology_graph_test.py")  # needs knowledge-base; skipped in CI when absent

from technology_graph_guard import validate  # noqa: E402


class TechnologyGraphGuardTests(unittest.TestCase):
    def test_repository_graph_is_valid(self):
        report = validate(ROOT)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["counts"]["nodes"], 26)
        self.assertEqual(report["counts"]["edges"], 38)
        self.assertEqual(report["counts"]["flows"], 6)
        json.dumps(report)

    def test_unsafe_evidence_path_is_rejected_without_echoing_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            architecture = root / "systems" / "architecture"
            architecture.mkdir(parents=True)
            (architecture / "technology-taxonomy.md").write_text("# Taxonomy\n", encoding="utf-8")
            (architecture / "solo-empire-technology-graph.md").write_text("# Graph\n", encoding="utf-8")

            taxonomy = {
                "version": 1,
                "kind": "technology-taxonomy",
                "status": "current",
                "canonical_markdown": "technology-taxonomy.md",
                "node_kinds": ["component"],
                "layers": [{"id": "application-workflow-integration", "name": "Application"}],
                "domains": [{"id": "enterprise-work", "name": "Enterprise"}],
                "capability_families": [{"id": "application-workflow", "name": "Workflow"}],
                "interface_families": [{"id": "software", "name": "Software"}],
                "cross_cutting": [{"id": "operability-evidence", "name": "Evidence"}],
                "flow_types": ["control"],
                "edge_types": ["calls"],
                "node_record": {
                    "required_fields": [
                        "id",
                        "kind",
                        "domain",
                        "capability",
                        "layer",
                        "owner_boundary",
                        "interfaces",
                        "flows",
                        "cross_cutting",
                        "status",
                    ]
                },
            }
            graph = {
                "version": 1,
                "kind": "technology-graph",
                "status": "current",
                "graph_id": "fixture-graph",
                "taxonomy": "technology-taxonomy.yaml",
                "canonical_markdown": "solo-empire-technology-graph.md",
                "as_of": "2026-09-06",
                "status_values": ["current"],
                "node_schema": {"required_fields": [
                    "id",
                    "kind",
                    "name",
                    "status",
                    "layer",
                    "domain",
                    "capability",
                    "owner_boundary",
                    "interfaces",
                    "flows",
                    "cross_cutting",
                    "evidence",
                ]},
                "nodes": [{
                    "id": "fixture-node",
                    "kind": "component",
                    "name": "Fixture node",
                    "status": "current",
                    "layer": "application-workflow-integration",
                    "domain": ["enterprise-work"],
                    "capability": ["application-workflow"],
                    "owner_boundary": "fixture-boundary",
                    "interfaces": ["software"],
                    "flows": ["control"],
                    "cross_cutting": ["operability-evidence"],
                    "evidence": ["data/raw/credentials.json"],
                }],
                "edges": [],
                "flows": [],
                "boundary_rules": [],
                "known_gaps": [],
            }
            (architecture / "technology-taxonomy.yaml").write_text(
                json.dumps(taxonomy), encoding="utf-8"
            )
            (architecture / "solo-empire-technology-graph.yaml").write_text(
                json.dumps(graph), encoding="utf-8"
            )

            report = validate(root)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("outside the allowed evidence boundary" in error for error in report["errors"])
        )
        self.assertNotIn("credentials", " ".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
