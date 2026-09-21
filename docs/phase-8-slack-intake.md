# Phase 8 first milestone: issues from Slack

The repository's original Phase 5 completed the Slack delivery worker. Under the
revised roadmap that is Phase 7. Phase 8 now begins with Slack channel intake,
following the operator's request. This milestone supports new human issue posts;
the full generic monitoring lifecycle remains unfinished.

## What happens

Post a new top-level text message in a configured issues channel. Slack sends a
signed event to FastAPI. After authenticating and authorizing the source, the backend
creates one open incident with the channel's configured service and severity.
The first line supplies the title (up to 200 characters); the full text becomes
the description (maximum 5,000 characters). The incident appears in the existing
console and its creation event includes Slack app/workspace/channel/event/message/
author IDs. Source timestamps are opaque message identity, not our audit clock.

Retries and duplicate events produce one incident and one notification intent.
Our existing outbox worker notifies Slack asynchronously. It does not repeat the
issue's free text or expand mentions. Bots, replies, edits/deletions, shared channels,
and file-only posts do not create incidents. Editing or deleting a Slack post does
not change an incident; operators continue to manage it in the application.

## Configure the Slack app

1. Use a Slack app in the intended workspace and invite its bot to the dedicated
   issues channel. Choose a non-shared channel whose messages may be visible to
   this application's incident readers. This MVP has no separate per-channel
   human visibility model or history backfill.
2. Subscribe to the bot event `message.channels` for a public channel with
   `channels:history`, or `message.groups` for a private channel with
   `groups:history`. Grant only the needed scope and reinstall after scope changes.
   See [Slack message events](https://docs.slack.dev/reference/events/message/).
3. Find the app ID, workspace/team ID, and channel ID (IDs, not display names).
   Put the app's **Signing Secret** from Basic Information in the deployment
   secret manager or ignored local `.env`. It is different from a bot token,
   OAuth client secret, legacy verification token, and `SLACK_WEBHOOK_URL`.
4. Set these server-side values, substituting the real non-secret IDs/service:

   ```dotenv
   SLACK_INTAKE__ENABLED=true
   SLACK_INTAKE__APP_ID=A123ABC
   SLACK_INTAKE__TEAM_ID=T123ABC
   SLACK_INTAKE__CHANNELS=[{"channel_id":"C123ABC","service":"checkout","severity":"medium"}]
   ```

   Set `SLACK_INTAKE__SIGNING_SECRET` separately as a secret; there is deliberately
   no signing-secret value in the example. Configuration is checked at startup.
   Restart after changing bindings or rotating the secret. At most 20 channels
   may be configured, each once. A stable channel binding survives key rotation.
5. Apply the additive migration with `make migrate`, then restart FastAPI. Deploy
   behind HTTPS with a matching `ALLOWED_HOSTS` and `API_PUBLIC_URL`. For TLS
   termination, configure Uvicorn's trusted proxy IPs to the actual proxy only;
   never use a wildcard. Strip incoming forwarding headers at the trusted edge.
   The route uses the server ASGI scheme/client, not raw forwarded headers.
6. In Slack Event Subscriptions, set the Request URL to
   `https://<api-host>/api/v1/integrations/slack/events`. The signed URL challenge
   is verified before being echoed. Configure the API before clicking verification.
   This route goes directly to FastAPI and is intentionally absent from the
   browser/agent API client. No generic `/alerts/webhook` endpoint is enabled.
7. Post a synthetic issue, open the incident console, and inspect the source IDs
   in the audit timeline. Enable the existing outbound worker if delivery is
   desired; see [the delivery runbook](phase-5-outbox.md). Keep the intake and
   notification channels separate for readability; bot suppression also prevents
   loops if they are the same channel.

No real Slack app or external workspace was configured by this code change.
Existing channel posts require a separately designed backfill operation.

## Operations and verification

Disabled intake returns 404. Invalid signatures/timestamps return 401, unauthorized
source bindings return 403, malformed payloads return 400, oversized bodies return
413, and rate limits return 429. Storage/timeouts return 503 so Slack can retry.
Do not suppress Slack retries based on its unsigned retry headers. Success is
`200 {"ok":true}`; replays add `Idempotent-Replayed: true`. It means the transaction
committed or the event was deliberately ignored, not that the outbox was delivered.

Keep system clocks synchronized. Monitor error codes, backlog, latency, and Slack
subscription status: Slack retries are finite, so a prolonged outage can lose
events and requires an explicit reconciliation process. The pre-authentication
budget is globally bounded per process and can be exhausted by hostile traffic;
use an edge body/connection/rate limit as well. Do not log headers, bodies, signing
secrets, raw webhook URLs, or SQL exception parameters. Never grant source principals
general incident-write or action scopes to work around an RLS/configuration error.

```bash
make check
uv run --project apps/api pytest apps/api/tests/integration/test_slack_intake.py
```

The suite exercises valid/invalid native signatures, past/future timestamps,
workspace/app/channel checks, untrusted forwarding headers, malformed/bounded input,
loop suppression, source throttles, database rollback, concurrent retries with two
connections, changed-payload rejection, source-isolated RLS, and the existing human
read/timeline path. No live Slack workspace is contacted by these tests.

### Verification record (2026-09-17)

- `make check`: passed, including lint/types/client drift, 186 unit/API/worker tests,
  74 PostgreSQL integration tests (migration downgrade/upgrade/drift and RLS), and
  23 frontend component tests.
- Existing Playwright console/approval/accessibility journeys: 8 passed.
- Gitleaks v8.30.1, tracked/new files and local planning documents: no leaks.
- Live Slack delivery/installation has not been exercised; activation requires the
  operator configuration above. Phase 8's remaining generic alert lifecycle is
  still planned.
