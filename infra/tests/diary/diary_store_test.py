#!/usr/bin/env python3
"""The diary writes to the hosted knowledge store when it is configured (SPEC-002 phase 1)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "workflows"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "utils"))

import daily_diary  # noqa: E402
import knowledge_store  # noqa: E402
from daily_diary import build_daily_diary, record_conversation_summary, write_daily_diary  # noqa: E402

DAY = "2026-11-03"
SUMMARIES = "diary/entries/2026/11/03/conversation-summaries.json"


class FakeStore:
    """The store contract the diary needs: get -> (bytes, version) | None, put with if_version."""

    def __init__(self) -> None:
        self.docs: dict[str, tuple[bytes, int]] = {}
        self.conflicts_left = 0
        self.puts: list[tuple[str, int | None]] = []

    def get(self, path: str) -> tuple[bytes, int] | None:
        return self.docs.get(path)

    def put(self, path: str, content: bytes, *, if_version: int | None) -> int:
        self.puts.append((path, if_version))
        current = self.docs.get(path, (b"", 0))[1]
        if self.conflicts_left:
            self.conflicts_left -= 1
            # Another writer got there first.
            self.docs[path] = (json.dumps([{"id": "other", "topic": "t", "question_summary": "q2",
                                            "outcome_summary": "o2", "next_step": ""}]).encode(), current + 1)
            raise knowledge_store.StoreConflict()
        if if_version is not None and if_version != current:
            raise knowledge_store.StoreConflict()
        self.docs[path] = (content, current + 1)
        return current + 1


def record(root: str, **changes: str) -> dict:
    fields = {
        "day": DAY,
        "topic": "store",
        "question_summary": "move diary to the store",
        "outcome_summary": "diary now writes to the hosted store",
        "entries_root": root,
        **changes,
    }
    return record_conversation_summary(**fields)


class DiaryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        # The daily note also reads the Tasks API; these tests are about the store only.
        tasks = patch.object(
            daily_diary, "_task_sections", return_value=([], [], {"open_tasks": 0, "blocked_tasks": 0, "overdue_tasks": 0})
        )
        tasks.start()
        self.addCleanup(tasks.stop)

    def test_a_summary_is_written_to_the_store_and_kept_locally(self) -> None:
        store = FakeStore()
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            result = record(root)
            self.assertEqual(result["store"], "synced")
            stored = json.loads(store.docs[SUMMARIES][0])
            self.assertEqual([row["id"] for row in stored], [result["id"]])
            local = Path(root, "2026/11/03/conversation-summaries.json")
            self.assertEqual(json.loads(local.read_text(encoding="utf-8")), stored)
            self.assertEqual(store.puts, [(SUMMARIES, 0)])

    def test_summaries_from_another_machine_are_merged_not_overwritten(self) -> None:
        store = FakeStore()
        other = [{"id": "elsewhere", "topic": "x", "question_summary": "q", "outcome_summary": "o", "next_step": ""}]
        store.docs[SUMMARIES] = (json.dumps(other).encode(), 4)
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            result = record(root)
            stored = json.loads(store.docs[SUMMARIES][0])
            self.assertEqual([row["id"] for row in stored], ["elsewhere", result["id"]])
            self.assertEqual(store.puts[-1], (SUMMARIES, 4))
            # The daily note is built from the merged list.
            payload = build_daily_diary(as_of=DAY, repo_root=tempfile.gettempdir(), entries_root=root)
            self.assertEqual(payload["overview"]["conversation_summaries"], 2)

    def test_a_concurrent_write_is_retried_on_the_newer_version(self) -> None:
        store = FakeStore()
        store.conflicts_left = 1
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            result = record(root)
            self.assertEqual(result["store"], "synced")
            ids = [row["id"] for row in json.loads(store.docs[SUMMARIES][0])]
            self.assertEqual(ids, ["other", result["id"]])

    def test_a_store_failure_keeps_the_local_entry_and_says_so(self) -> None:
        class Down(FakeStore):
            def get(self, path: str):
                raise knowledge_store.StoreUnavailable("503")

        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=Down()):
            result = record(root)
            self.assertEqual(result["status"], "recorded")
            self.assertEqual(result["store"], "failed: 503")
            self.assertTrue(Path(root, "2026/11/03/conversation-summaries.json").is_file())

    def test_without_a_store_the_diary_stays_local(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=None):
            self.assertEqual(record(root)["store"], "off")

    def test_the_daily_note_is_uploaded_too(self) -> None:
        store = FakeStore()
        store.docs["diary/entries/2026/11/03/auto-daily.md"] = (b"old", 2)
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            payload = build_daily_diary(as_of=DAY, repo_root=tempfile.gettempdir(), entries_root=root)
            write_daily_diary(payload, root)
            content, version = store.docs["diary/entries/2026/11/03/auto-daily.md"]
            self.assertEqual(version, 3)
            self.assertIn(DAY, content.decode("utf-8"))


    def test_the_daily_note_uses_summaries_recorded_elsewhere(self) -> None:
        store = FakeStore()
        other = [{"id": "elsewhere", "topic": "x", "question_summary": "from the laptop", "outcome_summary": "o", "next_step": ""}]
        store.docs[SUMMARIES] = (json.dumps(other).encode(), 1)
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            self.assertEqual(daily_diary.main(["--date", DAY, "--entries-root", root, "--write", "--json"]), 0)
            note = store.docs["diary/entries/2026/11/03/auto-daily.md"][0].decode("utf-8")
            self.assertIn("from the laptop", note)

    def test_a_preview_writes_nothing_and_a_failed_refresh_does_not_upload(self) -> None:
        store = FakeStore()
        other = [{"id": "elsewhere", "topic": "x", "question_summary": "q", "outcome_summary": "o", "next_step": ""}]
        store.docs[SUMMARIES] = (json.dumps(other).encode(), 1)
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(daily_diary.main(["--date", DAY, "--entries-root", root, "--json"]), 0)
            self.assertFalse(Path(root, "2026").exists())
            self.assertEqual(store.puts, [])
            # The preview still counts the summary recorded on another machine.
            self.assertEqual(json.loads(out.getvalue())["overview"]["conversation_summaries"], 1)

        class Down(FakeStore):
            def get(self, path: str):
                raise knowledge_store.StoreUnavailable("503")

        down = Down()
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=down):
            self.assertEqual(daily_diary.main(["--date", DAY, "--entries-root", root, "--write", "--json"]), 0)
            self.assertTrue(Path(root, "2026/11/03/auto-daily.md").is_file())
            self.assertEqual(down.puts, [])

    def test_merged_summaries_stay_bounded_on_the_duplicate_path(self) -> None:
        store = FakeStore()
        many = [{"id": f"r{i}", "topic": "t", "question_summary": "q", "outcome_summary": "o", "next_step": ""}
                for i in range(daily_diary.MAX_CONVERSATION_SUMMARIES + 20)]
        store.docs[SUMMARIES] = (json.dumps(many).encode(), 1)
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            record(root, conversation_id="r119")
            rows = json.loads(store.docs[SUMMARIES][0])
            self.assertEqual(len(rows), daily_diary.MAX_CONVERSATION_SUMMARIES)


    def test_overlapping_bounded_copies_keep_the_newest_rows(self) -> None:
        row = lambda i: {"id": f"r{i}", "topic": "t", "question_summary": "q", "outcome_summary": "o", "next_step": ""}
        remote = [row(i) for i in range(51, 151)]
        local = [row(i) for i in range(1, 101)]
        merged = daily_diary.merge_summaries(remote, local)[-daily_diary.MAX_CONVERSATION_SUMMARIES:]
        self.assertEqual([r["id"] for r in merged], [f"r{i}" for i in range(51, 151)])
        # A row only this machine has lands after its neighbour, not at the end.
        mixed = daily_diary.merge_summaries([row(1), row(3)], [row(1), row(2)])
        self.assertEqual([r["id"] for r in mixed], ["r1", "r2", "r3"])

    def test_a_misconfigured_store_is_a_failed_sync_not_local_only(self) -> None:
        with patch.object(daily_diary, "open_store", side_effect=knowledge_store.StoreUnavailable("client.env must be chmod 600")):
            store = daily_diary.open_diary_store()
            self.assertIsNotNone(store)
            with tempfile.TemporaryDirectory() as root:
                result = record(root)
                self.assertTrue(result["store"].startswith("failed: config"))
        with patch.object(daily_diary, "open_store", return_value=None):
            self.assertIsNone(daily_diary.open_diary_store())

    def test_the_note_upload_is_rendered_from_the_summaries_current_at_upload(self) -> None:
        store = FakeStore()
        with tempfile.TemporaryDirectory() as root, patch.object(daily_diary, "open_diary_store", return_value=store):
            payload = build_daily_diary(as_of=DAY, repo_root=tempfile.gettempdir(), entries_root=root)
            # Another machine records a summary after this note was built.
            late = [{"id": "late", "topic": "x", "question_summary": "recorded late", "outcome_summary": "o", "next_step": ""}]
            store.docs[SUMMARIES] = (json.dumps(late).encode(), 1)
            rebuild = lambda: build_daily_diary(as_of=DAY, repo_root=tempfile.gettempdir(), entries_root=root,
                                                summaries=daily_diary.merged_summaries(root, DAY)[0])
            write_daily_diary(payload, root, rebuild=rebuild)
            note = store.docs["diary/entries/2026/11/03/auto-daily.md"][0].decode("utf-8")
            self.assertIn("recorded late", note)


class KnowledgeStoreConfigTest(unittest.TestCase):
    def test_configuration_comes_from_env_or_client_env_and_is_https_only(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            client = Path(home, "client.env")
            client.write_text("EMPIRE_KNOWLEDGE_API=https://k.example\nSOLO_WEB_PUBLIC_ORIGIN=https://w.example\n")
            client.chmod(0o600)
            env = {"EMPIRE_CLIENT_CONFIG": str(client)}
            self.assertEqual(knowledge_store.settings(env), ("https://k.example", "https://w.example"))
            self.assertIsNone(knowledge_store.settings({"EMPIRE_CLIENT_CONFIG": str(Path(home, "none"))}))
            self.assertIsNone(knowledge_store.settings({**env, "SOLO_DIARY_STORE": "off"}))
            off = Path(home, "off.env")
            off.write_text(client.read_text() + "SOLO_DIARY_STORE=off\n")
            off.chmod(0o600)
            self.assertIsNone(knowledge_store.settings({"EMPIRE_CLIENT_CONFIG": str(off)}))
            with self.assertRaises(knowledge_store.StoreUnavailable):
                knowledge_store.settings({**env, "EMPIRE_KNOWLEDGE_API": "http://k.example"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
