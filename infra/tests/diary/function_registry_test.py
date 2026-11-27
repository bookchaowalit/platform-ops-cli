#!/usr/bin/env python3
"""The role registries come from the architecture-docs store space when no local copy exists.

architecture-docs moved into the hosted store (SPEC-001 D3, 2026-11-26), so `task diary
--functions ...` must not need a checkout of it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "utils"))

import function_share  # noqa: E402
import knowledge_store  # noqa: E402

CLASSIFICATIONS = b"functions:\n  - id: build-creation\n  - id: operations-execution\n"
ALIGNMENT = (
    b"function_rotation:\n  active_stage: s1\n  stages:\n"
    b"    - id: s1\n      target_share: {build-creation: 60, operations-execution: 40}\n"
)


class FakeStore:
    def __init__(self, docs: dict[str, bytes]) -> None:
        self.docs = docs
        self.gets: list[str] = []

    def get(self, path: str) -> tuple[bytes, int] | None:
        self.gets.append(path)
        return (self.docs[path], 1) if path in self.docs else None


class RegistryFromStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        function_share._store_registry.cache_clear()
        self.addCleanup(function_share._store_registry.cache_clear)

    def patched(self, store: FakeStore | None):
        opened: list[dict] = []

        def open_store(environ=None, **kwargs):
            opened.append(kwargs)
            return store

        self.opened = opened
        return patch.multiple(
            function_share,
            ARCHITECTURE_ROOT=self.root,
            open_store=open_store,
        )

    def test_reads_function_ids_from_the_store_without_a_checkout(self) -> None:
        store = FakeStore({"docs/registries/ROLE-CLASSIFICATIONS.yaml": CLASSIFICATIONS})
        with self.patched(store):
            self.assertEqual(function_share.load_function_ids(), ["build-creation", "operations-execution"])
            self.assertEqual(function_share.parse_functions("build-creation"), ["build-creation"])
        self.assertEqual(self.opened[0].get("space"), "architecture-docs")
        self.assertIs(self.opened[0].get("diary"), False, "SOLO_DIARY_STORE=off must not hide the registry")

    def test_store_read_happens_once_per_process(self) -> None:
        store = FakeStore({"docs/registries/ROLE-CLASSIFICATIONS.yaml": CLASSIFICATIONS})
        with self.patched(store):
            function_share.load_function_ids()
            function_share.load_function_ids()
        self.assertEqual(len(store.gets), 1)

    def test_rotation_stage_comes_from_the_store_too(self) -> None:
        store = FakeStore({"docs/registries/ROLE-ECONOMIC-ALIGNMENT.yaml": ALIGNMENT})
        with self.patched(store):
            self.assertEqual(function_share.load_stage(None)["id"], "s1")

    def test_local_checkout_wins_over_the_store(self) -> None:
        registries = self.root / "docs" / "registries"
        registries.mkdir(parents=True)
        (registries / "ROLE-CLASSIFICATIONS.yaml").write_bytes(b"functions:\n  - id: local-only\n")
        store = FakeStore({"docs/registries/ROLE-CLASSIFICATIONS.yaml": CLASSIFICATIONS})
        with self.patched(store):
            self.assertEqual(function_share.load_function_ids(), ["local-only"])
        self.assertEqual(store.gets, [])

    def test_explicit_path_is_read_as_given(self) -> None:
        path = self.root / "custom.yaml"
        path.write_bytes(CLASSIFICATIONS)
        with self.patched(None):
            self.assertEqual(function_share.load_function_ids(path), ["build-creation", "operations-execution"])

    def test_clear_error_when_neither_exists(self) -> None:
        with self.patched(None):
            with self.assertRaises(FileNotFoundError) as caught:
                function_share.load_function_ids()
        self.assertIn("EMPIRE_KNOWLEDGE_API", str(caught.exception))
        with self.patched(FakeStore({})):
            with self.assertRaises(FileNotFoundError) as caught:
                function_share.load_function_ids()
        self.assertIn("architecture-docs", str(caught.exception))


class StoreSettingsSwitchTest(unittest.TestCase):
    def test_diary_switch_only_applies_to_the_diary(self) -> None:
        env = {
            "EMPIRE_CLIENT_CONFIG": "/nonexistent",
            "EMPIRE_KNOWLEDGE_API": "https://knowledge.example",
            "SOLO_WEB_PUBLIC_ORIGIN": "https://issuer.example",
            "SOLO_DIARY_STORE": "off",
        }
        self.assertIsNone(knowledge_store.open_store(env))
        store = knowledge_store.open_store(env, space="architecture-docs", diary=False)
        self.assertIsNotNone(store)
        self.assertEqual(store.space, "architecture-docs")


if __name__ == "__main__":
    unittest.main()
