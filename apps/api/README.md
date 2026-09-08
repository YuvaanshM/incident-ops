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
