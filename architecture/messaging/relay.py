"""ADR-0008 outbox relay: publish unsent outbox rows to Kafka, then mark them sent.

Copy into the cell unchanged (cells own their code). Run it from the cell's job endpoint, which
the `jobs` queue calls every few minutes, so Neon can still scale to zero.

At-least-once: a crash after publish and before the UPDATE re-sends the row on the next run;
consumers dedupe on the envelope id (inbox table).

Ordering per message key: only one relay runs at a time (transaction-scoped advisory lock), and
after a failed publish every later row with the same key waits for the next run.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

# One relay per database; a second run returns at once instead of publishing out of order.
TRY_LOCK = "SELECT pg_try_advisory_xact_lock(hashtext('adr0008-outbox-relay'))"
# A key whose oldest unsent row has failed sends only that row, so its later rows never take
# batch slots from other keys (no head-of-line blocking across keys). A failed row waits for its
# backoff (`next_attempt_at`), so rows that keep failing cannot fill every batch either.
SELECT_UNSENT = (
    "SELECT o.id, o.topic, o.message_key, o.envelope FROM outbox o "
    "WHERE o.sent_at IS NULL "
    "AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= now()) "
    "AND NOT EXISTS ("
    "SELECT 1 FROM outbox f WHERE f.topic = o.topic AND f.message_key = o.message_key "
    "AND f.sent_at IS NULL AND f.attempts > 0 AND f.seq < o.seq) "
    "ORDER BY o.seq LIMIT %s"
)
MARK_SENT = "UPDATE outbox SET sent_at = now(), attempts = attempts + 1, last_error = NULL WHERE id = %s"
# Backoff 1, 2, 4 … 64 minutes from the failure itself (clock_timestamp, not the transaction
# start, which is fixed for a long batch); the exponent is capped first, so a row that failed thousands of
# times cannot overflow the interval and abort the whole batch.
MARK_FAILED = (
    "UPDATE outbox SET attempts = attempts + 1, last_error = %s, "
    "next_attempt_at = clock_timestamp() + interval '1 minute' * power(2, least(attempts, 6)) "
    "WHERE id = %s"
)
OLDEST_UNSENT = "SELECT EXTRACT(EPOCH FROM now() - min(created_at)) FROM outbox WHERE sent_at IS NULL"


PUBLISH_TIMEOUT_SECONDS = 15.0
# A publish may wait PUBLISH_TIMEOUT_SECONDS inside the transaction; allow more idle time than
# a pool's usual idle-in-transaction limit so a slow broker does not abort the whole batch.
RELAX_IDLE_TIMEOUT = "SELECT set_config('idle_in_transaction_session_timeout', '60s', true)"


class Publisher(Protocol):
    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        """Return once the broker acknowledged the message; raise otherwise."""


@dataclass
class RelayResult:
    sent: int
    failed: int


def relay_once(conn: Any, publisher: Publisher, batch_size: int = 100) -> RelayResult:
    """Publish one batch inside one transaction. `conn` is a psycopg 3 connection.

    `conn.transaction()` opens a real transaction even on an autocommit connection, so the
    transaction-scoped advisory lock holds for the whole batch (psycopg 3's `with conn:` would
    instead close the connection, and autocommit would release the lock after TRY_LOCK).
    """
    sent = failed = 0
    with conn.transaction():  # commits on success, rolls back on an exception
        cur = conn.cursor()
        cur.execute(RELAX_IDLE_TIMEOUT)
        cur.execute(TRY_LOCK)
        if not cur.fetchone()[0]:
            return RelayResult(sent=0, failed=0)
        cur.execute(SELECT_UNSENT, (batch_size,))
        rows = cur.fetchall()
        blocked: set[tuple[str, str]] = set()
        for row_id, topic, key, envelope in rows:
            if (topic, key) in blocked:
                continue  # an earlier event for this key failed; keep the order
            value = json.loads(envelope) if isinstance(envelope, str) else envelope
            try:
                publisher.publish(topic, key, value)
            except Exception as exc:  # noqa: BLE001 - recorded on the row, retried after backoff
                cur.execute(MARK_FAILED, (type(exc).__name__, row_id))
                blocked.add((topic, key))
                failed += 1
                continue
            cur.execute(MARK_SENT, (row_id,))
            sent += 1
    return RelayResult(sent=sent, failed=failed)


class KafkaPublisher:
    """confluent-kafka producer; kafka-python-ng cannot authenticate to Kafka 4.x brokers.

    `config` holds the librdkafka settings, e.g. bootstrap.servers, security.protocol=SASL_SSL,
    sasl.mechanism=SCRAM-SHA-256, sasl.username, sasl.password, ssl.ca.location.
    """

    def __init__(self, config: dict[str, str], timeout: float = PUBLISH_TIMEOUT_SECONDS):
        from confluent_kafka import Producer

        self.timeout = timeout
        self.producer = Producer({**config, "acks": "all", "enable.idempotence": True})

    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        result: dict[str, Any] = {}
        self.producer.produce(
            topic,
            key=key.encode(),
            value=json.dumps(value, separators=(",", ":")).encode(),
            on_delivery=lambda err, _msg: result.setdefault("err", err),
        )
        deadline = time.monotonic() + self.timeout
        while "err" not in result and time.monotonic() < deadline:
            self.producer.poll(0.2)
        if "err" not in result:
            raise TimeoutError("broker did not acknowledge")
        if result["err"] is not None:
            raise RuntimeError(result["err"].name())


def oldest_unsent_seconds(conn: Any) -> float:
    """Outbox lag for /readyz: 0 when nothing waits."""
    cur = conn.cursor()
    cur.execute(OLDEST_UNSENT)
    value = cur.fetchone()[0]
    return float(value or 0)
