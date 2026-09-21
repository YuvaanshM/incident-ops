import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

const incidentId = "10000000-0000-4000-8000-000000000001";
const jobId = "20000000-0000-4000-8000-000000000001";
const actionId = "30000000-0000-4000-8000-000000000001";

const incident = {
  id: incidentId,
  title: "Checkout latency above SLO",
  description: "Customers are waiting more than five seconds at checkout.",
  service: "checkout",
  severity: "high",
  status: "open",
  version: 1,
  created_by_type: "human",
  created_by_id: "operator@example.com",
  created_at: "2026-09-09T12:00:00Z",
  updated_at: "2026-09-09T12:42:00Z",
  resolved_at: null,
  closed_at: null,
  jobs: [
    {
      id: jobId,
      incident_id: incidentId,
      stable_key: "checkout-reindex",
      name: "Rebuild checkout search index",
      status: "failed",
      retry_count: 1,
      version: 2,
      last_error: "Synthetic timeout",
      created_at: "2026-09-09T12:05:00Z",
      updated_at: "2026-09-09T12:30:00Z",
    },
  ],
} as const;

const timelineEvent = {
  id: "50000000-0000-4000-8000-000000000001",
  incident_id: incidentId,
  action_request_id: actionId,
  event_type: "incident.updated",
  actor_type: "human",
  actor_id: "operator@example.com",
  request_id: "60000000-0000-4000-8000-000000000001",
  payload: { changes: { status: { from: "open", to: "investigating" } } },
  occurred_at: "2026-09-09T12:42:00Z",
};

const pendingAction = {
  id: actionId,
  incident_id: incidentId,
  action_type: "retry_failed_job",
  status: "pending",
  execution_mode: "human_approved",
  requested_by_type: "human",
  requested_by_id: "operator@example.com",
  idempotency_key: "40000000-0000-4000-8000-000000000001",
  base_incident_version: 2,
  parameters: { job_id: jobId },
  policy_version: "2026-09-05.v1",
  policy_snapshot: { decision: "require_approval" },
  rationale: "The failed job is safe to retry after mitigation.",
  outcome_code: null,
  outcome_payload: null,
  decided_by_type: null,
  decided_by_id: null,
  decided_at: null,
  created_at: "2026-09-09T12:43:00Z",
  updated_at: "2026-09-09T12:43:00Z",
} as const;

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    body: JSON.stringify(body),
    contentType: "application/json",
    status,
  });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/ui/incidents/*/alerts?*", (route) =>
    json(route, { items: [], next_cursor: null }),
  );
});

async function mockApprovalContext(page: Page, action = pendingAction) {
  await page.route(`**/api/ui/actions/${actionId}`, (route) =>
    json(route, action),
  );
  await page.route(`**/api/ui/incidents/${incidentId}`, (route) =>
    json(route, incident),
  );
}

test("create, investigate, request retry, and inspect the audit trail", async ({
  page,
}) => {
  let currentIncident: Record<string, unknown> = incident;
  await page.route("**/api/ui/incidents", async (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["idempotency-key"]).toBeTruthy();
    await json(route, currentIncident, 201);
  });
  await page.route(`**/api/ui/incidents/${incidentId}/timeline?*`, (route) =>
    json(route, { items: [timelineEvent], next_cursor: null }),
  );
  await page.route(
    `**/api/ui/incidents/${incidentId}/actions`,
    async (route) => {
      expect(route.request().headers()["idempotency-key"]).toBeTruthy();
      await json(route, pendingAction, 201);
    },
  );
  await page.route(`**/api/ui/incidents/${incidentId}`, async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as {
        expected_version: number;
      };
      expect(body.expected_version).toBe(1);
      currentIncident = { ...incident, status: "investigating", version: 2 };
    }
    await json(route, currentIncident);
  });

  await page.goto("/incidents/new");
  await page.getByLabel("Incident title").fill(incident.title);
  await page.getByLabel("Affected service").fill(incident.service);
  await page.getByLabel("Severity").selectOption(incident.severity);
  await page.getByLabel("What is happening?").fill(incident.description);
  await page.getByRole("button", { name: "Create incident" }).click();

  await expect(page).toHaveURL(`/incidents/${incidentId}`);
  await page.getByLabel("Status").selectOption("investigating");
  await page.getByRole("button", { name: "Save incident" }).click();
  await expect(page.getByText("Incident saved at version 2.")).toBeVisible();
  await page
    .getByLabel("Retry rationale")
    .fill("The failed job is safe to retry after mitigation.");
  await page.getByRole("button", { name: "Request retry" }).click();

  await expect(
    page.getByText("Retry request is pending human review."),
  ).toBeVisible();
  await expect(page.getByText("Incident updated")).toBeVisible();
  await expect(
    page.getByLabel("Audit log").getByText("2026-09-09 12:42:00 UTC"),
  ).toBeVisible();
});

test("a stale edit preserves the draft until deliberate resubmission", async ({
  page,
}) => {
  const draft = "My draft with confirmed database evidence.";
  const latest = {
    ...incident,
    description: "Another operator narrowed impact to US card payments.",
    version: 2,
  };
  let current: Record<string, unknown> = incident;
  let writes = 0;
  await page.route(`**/api/ui/incidents/${incidentId}/timeline?*`, (route) =>
    json(route, { items: [], next_cursor: null }),
  );
  await page.route(`**/api/ui/incidents/${incidentId}`, async (route) => {
    if (route.request().method() === "PATCH") {
      writes += 1;
      const body = route.request().postDataJSON() as {
        description: string;
        expected_version: number;
      };
      if (writes === 1) {
        expect(body.expected_version).toBe(1);
        current = latest;
        await json(
          route,
          {
            error: {
              code: "version_conflict",
              message: "The resource changed. Refresh it before retrying.",
              details: { current_version: 2 },
            },
          },
          409,
        );
        return;
      }
      expect(body).toMatchObject({ description: draft, expected_version: 2 });
      current = { ...latest, description: draft, version: 3 };
    }
    await json(route, current);
  });

  await page.goto(`/incidents/${incidentId}`);
  await page.getByLabel("Description").fill(draft);
  await page.getByRole("button", { name: "Save incident" }).click();
  await expect(
    page.getByRole("heading", { name: "Your draft is safe" }),
  ).toBeVisible();
  await expect(page.getByLabel("Description")).toHaveValue(draft);
  await page.getByRole("button", { name: "Review latest version" }).click();
  await expect(page.getByText("Latest version 2")).toBeVisible();
  await expect(page.getByLabel("Description")).toHaveValue(draft);
  await page.getByRole("button", { name: "Resubmit my draft" }).click();
  await expect(page.getByText("Incident saved at version 3.")).toBeVisible();
});

for (const decision of ["approve", "reject"] as const) {
  test(`a human can ${decision} a pending bounded request`, async ({
    page,
  }) => {
    const completed = {
      ...pendingAction,
      status: decision === "approve" ? "applied" : "rejected",
      outcome_code: decision === "approve" ? "job_retried" : "human_rejected",
      decided_by_type: "human",
      decided_by_id: "operator@example.com",
      decided_at: "2026-09-09T12:46:00Z",
    };
    await mockApprovalContext(page);
    await page.route(`**/api/ui/actions/${actionId}/${decision}`, (route) =>
      json(route, completed),
    );

    await page.goto(`/approvals/${actionId}`);
    if (decision === "approve") {
      await page.getByRole("button", { name: "Approve request" }).click();
      await expect(page.getByText("This request was applied.")).toBeVisible();
    } else {
      await page.getByRole("button", { name: "Reject request" }).click();
      await page.getByLabel("Rejection reason").fill("Risk remains too high.");
      await page.getByRole("button", { name: "Confirm rejection" }).click();
      await expect(page.getByText("This request was rejected.")).toBeVisible();
    }
    await expect(
      page.getByRole("button", { name: "Approve request" }),
    ).toHaveCount(0);
  });
}

test("an approval race refreshes to the other operator's result", async ({
  page,
}) => {
  let current: Record<string, unknown> = pendingAction;
  await page.route(`**/api/ui/actions/${actionId}`, (route) =>
    json(route, current),
  );
  await page.route(`**/api/ui/incidents/${incidentId}`, (route) =>
    json(route, incident),
  );
  await page.route(`**/api/ui/actions/${actionId}/approve`, async (route) => {
    current = {
      ...pendingAction,
      status: "rejected",
      outcome_code: "human_rejected",
      decided_by_type: "human",
      decided_by_id: "other-operator@example.com",
      decided_at: "2026-09-09T12:46:00Z",
    };
    await json(
      route,
      {
        error: {
          code: "action_not_pending",
          message: "The action is no longer pending.",
        },
      },
      409,
    );
  });

  await page.goto(`/approvals/${actionId}`);
  await page.getByRole("button", { name: "Approve request" }).click();

  await expect(
    page.getByText(
      "Another operator already decided this request. The latest result is shown below.",
    ),
  ).toBeVisible();
  await expect(page.getByText("other-operator@example.com")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve request" }),
  ).toHaveCount(0);
});

test("validation preserves input and forbidden approval access has no controls", async ({
  page,
}) => {
  await page.route("**/api/ui/incidents", (route) =>
    json(
      route,
      {
        error: {
          code: "validation_error",
          message: "The submitted values are not valid.",
          details: {
            fields: [
              {
                location: ["body", "title"],
                type: "value_error",
                message: "Use a more specific title.",
              },
            ],
          },
        },
      },
      422,
    ),
  );
  await page.goto("/incidents/new");
  await page.getByLabel("Incident title").fill("Latency");
  await page.getByLabel("Affected service").fill("checkout");
  await page.getByLabel("What is happening?").fill(incident.description);
  await page.getByRole("button", { name: "Create incident" }).click();
  await expect(page.getByLabel("Incident title")).toHaveValue("Latency");
  await expect(page.getByText("Use a more specific title.")).toBeVisible();

  await page.route("**/api/ui/actions?*", (route) =>
    json(
      route,
      { error: { code: "forbidden", message: "Approval scope is required." } },
      403,
    ),
  );
  await page.goto("/approvals");
  await expect(
    page.getByRole("heading", { name: "Approval access required" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /approve request/i }),
  ).toHaveCount(0);
});

test("key console routes have no serious automated accessibility violations", async ({
  page,
}) => {
  await page.route("**/api/ui/incidents?*", (route) =>
    json(route, { items: [incident], next_cursor: null }),
  );
  await page.route("**/api/ui/actions?*", (route) =>
    json(route, { items: [pendingAction], next_cursor: null }),
  );
  await page.route(`**/api/ui/incidents/${incidentId}`, (route) =>
    json(route, incident),
  );

  for (const path of ["/incidents", "/approvals"]) {
    await page.goto(path);
    await expect(page.locator("h1")).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter((violation) =>
        ["serious", "critical"].includes(violation.impact ?? ""),
      ),
    ).toEqual([]);
  }
});

test("the narrow ledger stays usable by keyboard without horizontal overflow", async ({
  page,
}) => {
  await page.route("**/api/ui/incidents?*", (route) =>
    json(route, { items: [incident], next_cursor: null }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/incidents");
  await expect(
    page.getByRole("heading", { name: "Active incidents" }),
  ).toBeVisible();

  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to main content" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await expect(page.locator(".incident-table")).toBeHidden();
  await expect(page.locator(".incident-card-list")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("monitor recovery is visible without resolving the incident", async ({
  page,
}) => {
  const monitored = {
    ...incident,
    status: "open",
    monitoring_status: "RESOLVED",
    monitoring_updated_at: "2026-09-25T14:10:00Z",
  };
  const sourceTitle = "Monitor <script>alert(1)</script>";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(`**/api/ui/incidents/${incidentId}`, (route) =>
    json(route, monitored),
  );
  await page.route(`**/api/ui/incidents/${incidentId}/timeline?*`, (route) =>
    json(route, { items: [], next_cursor: null }),
  );
  await page.route(`**/api/ui/incidents/${incidentId}/alerts?*`, (route) =>
    json(route, {
      items: [
        {
          id: "80000000-0000-4000-8000-000000000001",
          alert_id: "80000000-0000-4000-8000-000000000002",
          incident_id: incidentId,
          source: "monitoring-demo-prod",
          started_at: "2026-09-25T14:00:00Z",
          event_version: 2,
          status: "RESOLVED",
          severity: "critical",
          title: sourceTitle,
          outcome: "resolution_recorded",
          received_at: "2026-09-25T14:10:00Z",
        },
      ],
      next_cursor: null,
    }),
  );

  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/incidents/${incidentId}`);
    await expect(
      page.getByRole("heading", { name: "Monitoring resolved" }),
    ).toBeVisible();
    await expect(
      page.getByText(/human still needs to confirm and resolve/),
    ).toBeVisible();
    await expect(page.getByText(sourceTitle)).toBeVisible();
    await expect(page.locator(".job-card script")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: test.info().outputPath(`monitoring-${width}.png`),
    });
  }
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
  expect(errors).toEqual([]);
});
