#!/usr/bin/env python3
"""The role registries come from the architecture-docs store space when no local copy exists.

architecture-docs moved into the hosted store (SPEC-001 D3, 2026-11-26), so `task diary
--functions ...` must not need a checkout of it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
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

    def test_malformed_ids_are_a_broken_registry(self) -> None:
        for body in (b"functions:\n  - id: null\n", b"functions:\n  - id: 3\n", b"functions:\n  - id: ''\n",
                     b"functions: nope\n", b"- just a list\n"):
            function_share._store_registry.cache_clear()
            store = FakeStore({"docs/registries/ROLE-CLASSIFICATIONS.yaml": body})
            with self.subTest(body=body), self.patched(store):
                with self.assertRaises(ValueError):
                    function_share.load_function_ids()

    def test_clear_error_when_neither_exists(self) -> None:
        with self.patched(None):
            with self.assertRaises(FileNotFoundError) as caught:
                function_share.load_function_ids()
        self.assertIn("EMPIRE_KNOWLEDGE_API", str(caught.exception))
        with self.patched(FakeStore({})):
            with self.assertRaises(FileNotFoundError) as caught:
                function_share.load_function_ids()
        self.assertIn("architecture-docs", str(caught.exception))


class SnapshotLookupTest(unittest.TestCase):
    """Without SOLO_ARCHITECTURE_ROOT, the hub's local snapshot of the store space is used offline."""

    def test_snapshot_is_read_before_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "architecture-docs"
            (snapshot / "docs" / "registries").mkdir(parents=True)
            (snapshot / ".snapshot.json").write_text(json.dumps({"taken_at": "2999-01-01T00:00:00Z"}))
            (snapshot / "docs" / "registries" / "ROLE-CLASSIFICATIONS.yaml").write_bytes(CLASSIFICATIONS)
            env = {key: value for key, value in os.environ.items() if key != "SOLO_ARCHITECTURE_ROOT"}
            env["SOLO_ARCHITECTURE_SNAPSHOT"] = str(snapshot)
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(function_share, "ARCHITECTURE_ROOT", None), \
                    patch.object(function_share, "sibling_repo_root", lambda repo: snapshot.parent / "none"), \
                    patch.object(function_share, "open_store", lambda *a, **k: self.fail("store must not be called")):
                # No reload: the roots are resolved per call, and reloading _env would run
                # load_env() and bring back a SOLO_ARCHITECTURE_ROOT from infra/.env.
                self.assertEqual(function_share.load_function_ids(), ["build-creation", "operations-execution"])


class OfflineDiaryTest(unittest.TestCase):
    """An unreadable registry keeps the summary (without tags) instead of losing the entry."""

    def test_summary_is_kept_without_tags_when_the_registry_is_unavailable(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "workflows"))
        import daily_diary

        def unavailable(*args, **kwargs):
            raise knowledge_store.StoreUnavailable("unreachable")

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"SOLO_DIARY_STORE": "off"}), \
                patch.object(daily_diary, "load_function_ids", unavailable), \
                redirect_stderr(io.StringIO()) as err:
            result = daily_diary.record_conversation_summary(
                day="2026-11-27", topic="offline", question_summary="q", outcome_summary="o",
                functions="build-creation", entries_root=tmp,
            )
            rows = json.loads((Path(tmp) / "2026" / "11" / "27" / "conversation-summaries.json").read_text())
        self.assertEqual(result["status"], "recorded")
        self.assertNotIn("functions", rows[0])
        self.assertIn("functions not recorded", err.getvalue())

    def test_unreadable_or_broken_registry_keeps_the_summary(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "workflows"))
        import daily_diary
        import yaml

        for error in (PermissionError("denied"), yaml.YAMLError("bad"), KeyError("functions"), UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")):
            def broken(*args, error=error, **kwargs):
                raise error

            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict(os.environ, {"SOLO_DIARY_STORE": "off"}), \
                    patch.object(daily_diary, "load_function_ids", broken), \
                    redirect_stderr(io.StringIO()) as err:
                result = daily_diary.record_conversation_summary(
                    day="2026-11-27", topic="broken", question_summary="q", outcome_summary="o",
                    functions="build-creation", entries_root=tmp,
                )
                self.assertEqual(result["status"], "recorded")
                self.assertIn("functions not recorded", err.getvalue())

    def test_unknown_function_still_fails(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "workflows"))
        import daily_diary

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"SOLO_DIARY_STORE": "off"}), \
                patch.object(daily_diary, "load_function_ids", lambda *a, **k: ["build-creation"]):
            with self.assertRaises(ValueError):
                daily_diary.record_conversation_summary(
                    day="2026-11-27", topic="offline", question_summary="q", outcome_summary="o",
                    functions="no-such-function", entries_root=tmp,
                )


class LookupOrderTest(unittest.TestCase):
    """Local candidates are resolved per call (after infra/.env loads): override, snapshot, checkout."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        function_share._store_registry.cache_clear()
        self.addCleanup(function_share._store_registry.cache_clear)

    def tree(self, name: str, *, stamp: bool = False, registry: bytes | None = None) -> Path:
        root = self.base / name
        (root / "docs" / "registries").mkdir(parents=True)
        if stamp:
            (root / ".snapshot.json").write_text(json.dumps({"taken_at": "2999-01-01T00:00:00Z"}))
        if registry is not None:
            (root / "docs" / "registries" / "ROLE-CLASSIFICATIONS.yaml").write_bytes(registry)
        return root

    def no_store(self):
        return patch.object(function_share, "open_store", lambda *a, **k: self.fail("store must not be called"))

    def test_override_set_after_import_is_honoured(self) -> None:
        override = self.tree("override", registry=b"functions:\n  - id: from-env-file\n")
        env = {key: value for key, value in os.environ.items() if key != "SOLO_ARCHITECTURE_ROOT"}
        env["SOLO_ARCHITECTURE_ROOT"] = str(override)  # what load_env() puts there from infra/.env
        with patch.dict(os.environ, env, clear=True), self.no_store():
            self.assertEqual(function_share.load_function_ids(), ["from-env-file"])

    def test_checkout_is_used_when_the_snapshot_lacks_the_file(self) -> None:
        snapshot = self.tree("snapshot", stamp=True)
        checkout = self.tree("checkout", registry=b"functions:\n  - id: from-checkout\n")
        env = {key: value for key, value in os.environ.items() if key != "SOLO_ARCHITECTURE_ROOT"}
        env["SOLO_ARCHITECTURE_SNAPSHOT"] = str(snapshot)
        with patch.dict(os.environ, env, clear=True), self.no_store(), \
                patch.object(function_share, "sibling_repo_root", lambda repo: checkout):
            self.assertEqual(function_share.load_function_ids(), ["from-checkout"])


class StoreReadErrorTest(unittest.TestCase):
    def test_cut_response_body_is_store_unavailable(self) -> None:
        import http.client

        class Cut:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                raise http.client.IncompleteRead(b"{", 10)

        store = knowledge_store.KnowledgeStore("https://knowledge.example", "https://issuer.example", space="architecture-docs")
        with patch.object(knowledge_store, "mint", lambda *a, **k: "t"), patch.object(store._opener, "open", lambda *a, **k: Cut()):
            with self.assertRaises(knowledge_store.StoreUnavailable):
                store.get("docs/registries/ROLE-CLASSIFICATIONS.yaml")


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
