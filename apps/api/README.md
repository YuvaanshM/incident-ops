# Incident Ops API

The FastAPI adapter and worker entry point for Incident Ops. The repository root
README contains setup, architecture, and security documentation. Database
migrations live in `migrations/` and are run from the repository root with:

```bash
uv run --project apps/api alembic -c apps/api/alembic.ini upgrade head
```

Use only a disposable database whose name ends in `_test` for integration tests.
The test fixture derives `incident_ops_test` from the normal local database URL
unless `TEST_DATABASE_URL` is provided explicitly.

Phase 3 routes are documented at `/docs` outside production. For local manual
testing, set `DEVELOPMENT_AUTH_ENABLED=true` in the ignored `.env` and authorize
with `human-demo` or `agent-demo`. These are public fixture values, not secrets;
the verifier cannot load in production. Follow the root
[`docs/phase-3-api.md`](../../docs/phase-3-api.md) walkthrough.
