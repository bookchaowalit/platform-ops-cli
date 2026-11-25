#!/usr/bin/env python3
"""Offline tests for the BA/SA architecture provider readiness contract."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from architecture_provider_readiness import (  # noqa: E402
    build_report,
    safe_provider_url,
    safe_provider_token,
    validate_archimate_model,
    validate_catalog_descriptor,
)


class ArchitectureProviderReadinessTests(unittest.TestCase):
    def test_repository_descriptor_is_valid_without_network(self):
        report = build_report(ROOT, env={})
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["mode"], "local-only")
        self.assertEqual(report["catalog_descriptor"]["state"], "valid")
        self.assertEqual(report["catalog_descriptor"]["resource_count"], 1)
        self.assertEqual(report["archimate_model"]["state"], "valid")
        self.assertEqual(report["archimate_model"]["view_count"], 1)
        self.assertEqual(
            [item["state"] for item in report["providers"]],
            ["disabled", "disabled"],
        )
        self.assertEqual(report["safety"]["network"], "none")

    def test_offline_mode_skips_configured_provider(self):
        report = build_report(
            ROOT,
            env={
                "ARCHITECTURE_BACKSTAGE_CATALOG_URL": "http://127.0.0.1:7007/api/catalog",
            },
            offline=True,
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["providers"][0]["state"], "skipped")
        self.assertEqual(report["safety"]["network"], "none")

    def test_provider_url_rejects_credentials_and_query(self):
        with_credentials = safe_provider_url("https://user:secret@example.test/api")
        with_query = safe_provider_url("https://example.test/api?token=secret")
        self.assertEqual(with_credentials["state"], "invalid")
        self.assertEqual(with_query["state"], "invalid")
        self.assertNotIn("secret", str(with_credentials))
        self.assertNotIn("secret", str(with_query))

    def test_provider_token_is_runtime_only_and_never_echoed(self):
        invalid = safe_provider_token("token with whitespace")
        self.assertEqual(invalid["state"], "invalid")
        self.assertNotIn("token with whitespace", str(invalid))
        self.assertEqual(safe_provider_token("runtime-token")["state"], "configured")

    def test_offline_token_configuration_does_not_send_credentials(self):
        report = build_report(
            ROOT,
            env={
                "ARCHITECTURE_OPENMETADATA_API_URL": "http://127.0.0.1:8585/api",
                "ARCHITECTURE_OPENMETADATA_API_TOKEN": "runtime-token",
            },
            offline=True,
        )
        provider = report["providers"][1]
        self.assertEqual(provider["state"], "skipped")
        self.assertTrue(provider["credential_configured"])
        self.assertFalse(provider["credentials_sent"])
        self.assertFalse(report["safety"]["credentials_sent"])

    def test_invalid_descriptor_is_reported_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog-info.yaml"
            path.write_text(
                "apiVersion: backstage.io/v1alpha1\n"
                "kind: Component\n"
                "metadata:\n"
                "  name: bad_component\n"
                "spec:\n"
                "  owner: user:default/test\n",
                encoding="utf-8",
            )
            report = validate_catalog_descriptor(path)
        self.assertEqual(report["state"], "invalid")
        self.assertTrue(report["errors"])
        self.assertNotIn("bad_component", " ".join(report["errors"]))

    def test_archimate_model_is_structurally_valid(self):
        model = validate_archimate_model(
            ROOT / "systems" / "architecture" / "models" / "solo-empire-system-context.archimate"
        )
        self.assertEqual(model["state"], "valid")
        self.assertEqual(model["element_count"], 63)
        self.assertEqual(model["relationship_count"], 31)

    def test_invalid_archimate_model_is_reported_without_content_echo(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.archimate"
            path.write_text(
                '<archimate:model xmlns:archimate="http://www.archimatetool.com/archimate" '
                'version="5.10.0" data="private-value" />',
                encoding="utf-8",
            )
            model = validate_archimate_model(path)
        self.assertEqual(model["state"], "invalid")
        self.assertTrue(model["errors"])
        self.assertNotIn("private-value", str(model))


if __name__ == "__main__":
    unittest.main()
