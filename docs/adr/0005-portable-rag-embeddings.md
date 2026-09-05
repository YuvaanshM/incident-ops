# ADR-0005: Portable RAG embeddings

Status: Accepted

Date: 2026-09-05

## Context

PostgreSQL needs a fixed dimension for `vector(n)`. The demo may use OpenAI or
Google for embeddings and may use a Gemini Flash model for advisory generation.
Embedding vectors from different model spaces cannot be compared even when
their lengths match.

## Decision

- Store historical-incident embeddings as `vector(1536)`.
- Keep embedding generation behind an application port with OpenAI and Google
  adapters introduced in the retrieval phase.
- Record the embedding provider and exact model beside every vector. Retrieval
  filters to the configured provider/model and refuses a mixed corpus.
- Re-embed the complete small corpus when changing embedding models.
- Use exact cosine search for the initial 10-20 records. Add an ANN index only
  after measurements justify it.
- Keep answer generation behind a separate port so Gemini Flash can synthesize
  results regardless of which compatible embedding provider generated the
  active corpus.
- Permit free-tier providers only for synthetic demo records; real incident
  content requires an explicitly approved data-processing arrangement.

## Consequences

The schema supports OpenAI `text-embedding-3-small` and Google embedding models
configured to return 1,536 dimensions. Provider changes require a controlled
re-embedding operation but no vector-column migration.

