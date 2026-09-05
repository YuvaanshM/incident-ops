# ADR-0001: Monorepo and runtime choices

- Status: Accepted
- Date: 2026-09-03

## Context

The product has one human web adapter, one backend service with a separate worker
entry point, shared generated API types, and local PostgreSQL. Reproducible local
and CI behavior matters because later phases test transactions and concurrency.

## Decision

Use a pnpm workspace monorepo with `apps/web`, `apps/api`, and
`packages/api-client`. Pin CPython 3.14 by minor and resolve its newest patch with
uv. Pin Node.js 24 LTS by major and pnpm 11.25.0 in package metadata. Commit both
lockfiles. Use Next.js App Router with strict TypeScript and FastAPI with an
installable Python `src/` package. Use one pgvector-enabled PostgreSQL 18 service
for relational and vector data.

## Consequences

Local and CI commands share the same package-manager paths and dependency graphs.
The repository has two runtimes, but no extra queue, cache, or vector service.
Node current releases outside the LTS line are not supported by the project even
when installed on a contributor machine.
