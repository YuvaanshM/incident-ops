# Contributing

1. Read `PROJECT_SPEC.md`, the current phase in `IMPLEMENTATION_PLAN.md`, and
   `AGENTS.md` before changing behavior.
2. Create a focused topic branch and keep commits small and conventional.
3. Add tests at the same boundary as the change, including negative security
   cases when input, identity, permissions, state, or persistence is involved.
4. Run `make check`; database changes must also run the integration suite and
   migration upgrade/downgrade checks introduced in Phase 2.
5. Review the diff for secrets, generated artifacts, unsafe SQL, authorization
   gaps, missing RLS policy changes, and errors that reveal internals.

Do not advance an implementation phase while its definition of done is failing.
