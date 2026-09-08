# Phase 3 API and Swagger walkthrough

Phase 3 exposes the ordinary human/product workflow under `/api/v1`. FastAPI
owns validation, authorization, policy decisions, idempotency, optimistic
concurrency, audit events, and transaction boundaries. PostgreSQL remains the
authority. The only executor is the app-owned demo-job retry; no Slack HTTP,
embedding request, LLM, WebMCP, MCP server, shell, or generic execution exists.

## Local Swagger setup

Set this local-only value in the ignored `.env` file:

```dotenv
DEVELOPMENT_AUTH_ENABLED=true
```

Then prepare and run the API:

```bash
make dev-db
make migrate
make seed
make dev-api
```

Open `http://localhost:8000/docs`. Select **Authorize** and enter one of these
public development fixture credentials:

- `human-demo`: incident read/write, action request/read/approve, and demo
  fixture access.
- `agent-demo`: incident read and action request only. It cannot approve,
  reject, close, critically escalate, or directly execute anything.

The local verifier is loaded only when `APP_ENV=development` and
`DEVELOPMENT_AUTH_ENABLED=true`. Production configuration rejects that flag.

Every POST/PATCH command below needs a fresh UUID in `Idempotency-Key`. Reusing
the same UUID with the same request returns the stored response. Reusing it with
a changed request returns `422 idempotency_key_reused_with_different_payload`.

## Headline retry flow

The additive seed has stable IDs:

- Low incident: `10000000-0000-4000-8000-000000000001`
- Its failed job: `20000000-0000-4000-8000-000000000001`

1. With `human-demo`, call `GET /api/v1/incidents/{incident_id}`. Confirm the
   incident version is `1` and the job is `failed` with `retry_count: 0`.
2. Switch to `agent-demo`. Call `POST /api/v1/incidents/{incident_id}/actions`
   with a fresh idempotency UUID and:

   ```json
   {
     "expected_incident_version": 1,
     "action_type": "retry_failed_job",
     "parameters": {
       "job_id": "20000000-0000-4000-8000-000000000001"
     },
     "rationale": "Retry the synthetic timeout."
   }
   ```

   Expect `201`, `status: "applied"`, `execution_mode: "auto"`, and
   `outcome_code: "job_queued"`.
3. Repeat exactly the same call with the same header UUID. Expect the exact same
   response and no second retry.
4. Switch to `human-demo`. Call
   `POST /api/v1/demo/jobs/{job_id}/simulate-failure` with a new idempotency
   UUID. Expect the job to become `failed` with `retry_count: 1`.
5. Switch to `agent-demo` and repeat the action request with a new idempotency
   UUID. Expect `201`, `status: "pending"`, and policy reason
   `human_approval_required`.
6. Switch to `human-demo`. Call `GET /api/v1/actions?status=pending`, then
   `GET /api/v1/actions/{action_id}` for the returned action.
7. Call `POST /api/v1/actions/{action_id}/approve` with a new idempotency UUID.
   Expect `200`, `status: "applied"`, and a job retry count of `2`.
8. Simulate another failure and request a third retry. Expect a recorded action
   with `status: "rejected"` and `outcome_code: "retry_limit_reached"`.
9. Call the incident timeline. Confirm it attributes the requests to
   `local-agent`, the review to `local-human`, and shows requested, pending,
   approved/applied, and rejected facts.

## Other checks worth trying

- Submit the same `PATCH /incidents/{id}` expected version twice with different
  idempotency UUIDs. The second call returns `409 version_conflict` and the
  current version/refetch path.
- Request `request_status_update`; it always becomes pending. Change the
  incident before approval, then approve it to receive a persisted `stale`
  outcome instead of overwriting newer state.
- Try approving with `agent-demo`; expect `403 forbidden`.
- Omit `Idempotency-Key`; expect `422 validation_error`.
- Send an unknown action type; expect `422 unsupported_action_type`.
- Add `status`, `execution_mode`, or `requested_by_id` to an action body; expect
  `422 validation_error`. The server always derives those values.

Seed loading is additive. Because audit events are immutable, recreate the
disposable local development database if the headline fixture has already
advanced and you need to repeat its exact initial state.
