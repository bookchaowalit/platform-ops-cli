# Messaging reference code (ADR-0008)

`outbox.sql` and `relay.py` are the copy-in reference implementation of the ADR-0008 outbox and
relay; `tests/` covers the relay. The envelope schema (`messaging/envelope.schema.json`) and the
adoption guide (`messaging/README.md`) are documents: they live in the architecture document tree
(store space `architecture-docs`), not here. Moved from architecture-docs at 3c7e564 (SPEC-001,
knowledge store `plans/architecture-docs-store/`).
