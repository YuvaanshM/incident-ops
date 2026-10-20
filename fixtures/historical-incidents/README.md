# Synthetic historical incidents

`incidents.json` contains twelve invented resolved examples across six services
and all four severities. Stable UUIDs and `seeded_demo_data` provenance identify
these as demo sources. There is no customer, company or production incident data.

Use `uv run --project apps/api python -m incident_ops.seed historical` from the
source checkout after configuring the embedding provider and running migrations.
Ingestion validates plain text, versions content changes and records an atomic
receipt/audit. Repeating the same manifest does not duplicate or re-embed it.

See [the retrieval runbook](../../docs/phase-11a-retrieval.md) for offline setup,
provider cost/data handling, full-corpus model changes, and citation semantics.
