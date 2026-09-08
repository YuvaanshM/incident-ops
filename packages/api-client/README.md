# API client

This package contains transport-only TypeScript types generated from FastAPI's
committed OpenAPI document and a thin `openapi-fetch` client. Business rules do
not belong here.

Regenerate after an API schema change:

```bash
make export-openapi
corepack pnpm --filter @incident-ops/api-client generate
```
