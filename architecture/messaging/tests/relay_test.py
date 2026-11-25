from __future__ import annotations

import importlib.util
import sys
import json
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("relay", Path(__file__).resolve().parents[1] / "relay.py")
relay = importlib.util.module_from_spec(SPEC)
sys.modules["relay"] = relay
SPEC.loader.exec_module(relay)


class FakeCursor:
    def __init__(self, rows, locked=True):
        self.rows = rows
        self.locked = locked
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        if self.executed and self.executed[-1][0] == relay.TRY_LOCK:
            return (self.locked,)
        return (self.rows,)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def transaction(self):
        self.transactions = getattr(self, "transactions", 0) + 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProducer:
    """A Publisher: publish() returns after the ack or raises."""

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.sent = []

    def publish(self, topic, key, value):
        self.sent.append((topic, key.encode(), value["id"]))
        if value["id"] in self.fail_ids:
            raise TimeoutError()


def row(row_id, key="pay_1"):
    return (row_id, "commerce.events.v1", key, {"id": row_id, "type": "payment.payment.succeeded"})


class RelayTest(unittest.TestCase):
    def test_marks_rows_sent_only_after_the_broker_ack(self):
        cur = FakeCursor([row("e1"), row("e2", key="pay_2")])
        producer = FakeProducer(fail_ids={"e2"})
        result = relay.relay_once(FakeConn(cur), producer)
        self.assertEqual((result.sent, result.failed), (1, 1))
        self.assertEqual(producer.sent[0], ("commerce.events.v1", b"pay_1", "e1"))
        self.assertIn((relay.MARK_SENT, ("e1",)), cur.executed)
        self.assertIn((relay.MARK_FAILED, ("TimeoutError", "e2")), cur.executed)
        self.assertNotIn((relay.MARK_SENT, ("e2",)), cur.executed)

    def test_a_failed_event_holds_back_later_events_with_the_same_key(self):
        cur = FakeCursor([row("e1"), row("e2"), row("e3", key="pay_2")])
        producer = FakeProducer(fail_ids={"e1"})
        result = relay.relay_once(FakeConn(cur), producer)
        self.assertEqual((result.sent, result.failed), (1, 1))
        self.assertEqual([s[2] for s in producer.sent], ["e1", "e3"])

    def test_takes_the_relay_lock_then_selects_the_batch(self):
        cur = FakeCursor([])
        conn = FakeConn(cur)
        relay.relay_once(conn, FakeProducer(), batch_size=7)
        self.assertEqual(conn.transactions, 1)  # the lock lives in an explicit transaction
        self.assertEqual(cur.executed[1][0], relay.TRY_LOCK)
        self.assertEqual(cur.executed[2], (relay.SELECT_UNSENT, (7,)))

    def test_select_orders_by_insertion_and_skips_rows_behind_a_failed_head(self):
        sql = relay.SELECT_UNSENT
        self.assertIn("ORDER BY o.seq", sql)
        self.assertIn("f.attempts > 0 AND f.seq < o.seq", sql)
        self.assertIn("o.next_attempt_at <= now()", sql)
        self.assertIn("next_attempt_at = clock_timestamp() +", relay.MARK_FAILED)

    def test_a_second_relay_returns_without_publishing(self):
        cur = FakeCursor([row("e1")], locked=False)
        producer = FakeProducer()
        result = relay.relay_once(FakeConn(cur), producer)
        self.assertEqual((result.sent, result.failed), (0, 0))
        self.assertEqual(producer.sent, [])

    def test_the_relay_raises_the_idle_in_transaction_limit_first(self):
        cur = FakeCursor([])
        relay.relay_once(FakeConn(cur), FakeProducer())
        self.assertEqual(cur.executed[0][0], relay.RELAX_IDLE_TIMEOUT)

    def test_backoff_caps_the_exponent_before_building_the_interval(self):
        self.assertIn("power(2, least(attempts, 6))", relay.MARK_FAILED)
        self.assertIn("clock_timestamp()", relay.MARK_FAILED)
        self.assertNotIn("now()", relay.MARK_FAILED)

    def test_oldest_unsent_is_zero_when_empty(self):
        self.assertEqual(relay.oldest_unsent_seconds(FakeConn(FakeCursor(None))), 0.0)


if __name__ == "__main__":
    unittest.main()
