# Phase 4 human console design

## Direction

The console uses an **incident ledger** layout with cool neutral surfaces,
a dark navigation rail, clear sans-serif headings, tabular numbers, and labeled
severity rails. The pre-Phase 5 polish adds rounded controls, quiet shadows,
consistent focus/press states, and compact navigation icons while preserving
the existing workflow and responsive route structure.

The interface has three visual layers only:

1. the persistent navigation rail/header;
2. the current ledger page;
3. an inset work surface for forms or decisions.

Color is semantic and sparse. Severity and action states always include text,
so red, amber, blue, and green never carry meaning alone. There are no
decorative gradients, glass panels, glowing effects, or background blobs.

## Route wireframes

### Wide screens

```text
┌──────────────┬──────────────────────────────────────────────────────────────┐
│ INCIDENT OPS │  INCIDENT LEDGER                           [New incident]   │
│              │  Search, service, severity, status                          │
│ Incidents 12 │──────────────────────────────────────────────────────────────│
│ Approvals  3 │  SEV  INCIDENT / SERVICE  STATUS  UPDATED  VERSION          │
│              │  ┃    Checkout latency   Investigating  08:42  v4           │
│ Operator     │  ┃    Description preview                                      │
│ Local/demo   │──────────────────────────────────────────────────────────────│
└──────────────┴──────────────────────────────────────────────────────────────┘

┌──────────────┬──────────────────────────────────────────────────────────────┐
│ navigation   │  INC-… / checkout                     HIGH · INVESTIGATING │
│              │  Checkout latency                           v4              │
│              │───────────────────────────────┬──────────────────────────────│
│              │  authoritative summary         │ update incident             │
│              │  demo jobs / request retry     │ preserve draft on conflict  │
│              │───────────────────────────────┴──────────────────────────────│
│              │  AUDIT LOG                                                    │
│              │  ● 08:42  human / incident.updated                           │
│              │  │          status: open → investigating                     │
│              │  ● 08:31  system / incident.created                          │
└──────────────┴──────────────────────────────────────────────────────────────┘
```

### Narrow screens

```text
┌────────────────────────────┐
│ INCIDENT OPS      [menu]   │
│ Incidents · Approvals      │
├────────────────────────────┤
│ INCIDENT LEDGER            │
│ Search / filters           │
│                            │
│ ┃ HIGH · INVESTIGATING     │
│ ┃ Checkout latency         │
│ ┃ checkout · updated 08:42 │
│ ┃ v4                       │
├────────────────────────────┤
│ next ledger entry          │
└────────────────────────────┘
```

Tables become ledger entries below 760px. Primary actions stay near their
heading; secondary metadata wraps below content instead of forcing horizontal
scroll. Approval decisions become full-width controls on narrow screens.

## Component and state inventory

| Component                      | Loading                            | Empty                     | Error / forbidden                                | Success / special                                  |
| ------------------------------ | ---------------------------------- | ------------------------- | ------------------------------------------------ | -------------------------------------------------- |
| `AppShell`                     | —                                  | —                         | session/setup notice                             | active route and operator context                  |
| `IncidentFilters`              | submit disabled only in navigation | all filters clear         | invalid query remains editable                   | filters encoded in URL                             |
| `IncidentTable` / mobile entry | ruled skeleton rows                | focused empty explanation | retry in place                                   | cursor appends and deduplicates IDs                |
| `IncidentForm`                 | submitted button only              | —                         | values preserved; field errors linked            | redirect to created incident                       |
| `IncidentSummary`              | page skeleton                      | —                         | not-found / forbidden / retry                    | authoritative status and version                   |
| `LifecycleControls`            | submitted button only              | —                         | draft preserved                                  | server-returned incident replaces display          |
| `ConflictPanel`                | refetch button only                | —                         | refetch error is local to panel                  | stale version, latest version, deliberate resubmit |
| `DemoJobPanel`                 | active command only                | no jobs copy              | backend policy/error code explained              | returned action state, never predicted             |
| `Timeline`                     | ruled skeleton events              | “No events recorded”      | retry in place                                   | actor, local time, exact UTC, readable payload     |
| `ApprovalQueue`                | ledger skeleton                    | no pending decisions      | 403 explains missing approval access             | cursor list with status, incident, and age filters |
| `ApprovalDetail`               | page skeleton                      | —                         | `action_not_pending` refreshes and explains race | approve/reject result announced                    |

## Interaction rules

- Reads and mutations go through narrow same-origin Next.js route handlers.
  Human access tokens remain server-side or in the Auth0 SDK's encrypted,
  HttpOnly session cookie.
- Mutations require an exact same-origin check, an explicit intent header, and
  a UUID idempotency key. The browser never chooses policy or execution mode.
- A `version_conflict` never causes an automatic retry. The current draft stays
  in the form, the latest record is fetched separately, and the operator must
  deliberately resubmit against the new version.
- Approval state is refreshed while the detail view is visible. If another
  operator wins, `action_not_pending` becomes a neutral race explanation, not a
  generic crash.
- Native controls and landmarks come first. Focus rings have at least 3:1
  contrast, status announcements use `role="status"`, errors use `role="alert"`,
  and destructive or consequential choices have complete text labels.
- Timeline payloads are rendered as escaped text. User content is never passed
  to `dangerouslySetInnerHTML`.
- Local timestamps are concise; the exact UTC timestamp is available in the
  visible detail or the native `title` text.
- Motion is limited to short opacity/position feedback and removed under
  `prefers-reduced-motion: reduce`.

## Authentication choice

Auth0 is the hosted OIDC provider selected for the production demo. Its Next.js
SDK owns the encrypted Secure/HttpOnly/SameSite session and authorization-code
flow. The API remains provider-neutral and validates issuer, audience, expiry,
signature, human actor claim, and scopes behind its existing `TokenVerifier`
port. Local development keeps the explicitly enabled public `human-demo`
credential on the server side only; it is never written to browser storage.

Production needs the `AUTH0_*` browser-session settings plus the corresponding
provider-neutral `OIDC_*` API validation settings listed in `.env.example`.
Configure an Auth0 Action to add the namespaced human actor claim selected by
`OIDC_ACTOR_TYPE_CLAIM`; the API refuses a missing or non-human value. The Auth0
access-token browser endpoint is disabled because the console uses a token-
mediating backend: only same-origin Next route handlers can retrieve and forward
the access token.

## Verification notes

- Playwright owns port `3107`, avoiding accidental reuse of an unrelated local
  site on the common development port.
- Browser journeys intercept only the owned Next-to-FastAPI transport boundary;
  FastAPI authorization, transactions, filters, and approval races retain their
  real PostgreSQL integration tests.
- Axe scans the incident and approval ledgers for serious/critical violations.
  The narrow-screen check also proves skip-link focus, the mobile ledger layout,
  and the absence of horizontal overflow at 390 px.
