-- ADR-0008 transactional outbox (Postgres). Copy into the cell's migrations unchanged.
-- Write the business row and the outbox row in the SAME transaction; relay.py publishes later.
CREATE TABLE IF NOT EXISTS outbox (
    id            text PRIMARY KEY,              -- envelope id
    seq           bigint GENERATED ALWAYS AS IDENTITY UNIQUE,  -- insertion order; the relay sorts on it
    topic         text NOT NULL,                 -- e.g. commerce.events.v1
    message_key   text NOT NULL,                 -- partition key, e.g. payment_ref
    envelope      jsonb NOT NULL,                -- full ADR-0008 envelope
    created_at    timestamptz NOT NULL DEFAULT now(),
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text,
    next_attempt_at timestamptz,                 -- backoff after a failed publish; NULL = due now
    sent_at       timestamptz
);

-- Unsent rows in order; also serves the "oldest unsent" lag check in /readyz.
CREATE INDEX IF NOT EXISTS outbox_unsent_idx ON outbox (seq) WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS outbox_unsent_key_idx ON outbox (topic, message_key, seq) WHERE sent_at IS NULL;

-- Per-key order needs the writers of one key to serialize, which they do when the transaction
-- updates (and so locks) the aggregate row before inserting its outbox row.

-- Consumer side: one row per processed envelope id, inserted in the side effect's transaction.
CREATE TABLE IF NOT EXISTS inbox (
    id            text NOT NULL,
    consumer      text NOT NULL,
    processed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, id)
);
