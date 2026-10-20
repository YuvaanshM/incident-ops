import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "safe-agent.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:3108", trace: "retain-on-failure" },
  projects: [
    {
      name: "fake-webmcp",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: { args: ["--disable-blink-features=WebMCP"] },
      },
    },
    ...(process.env.WEBMCP_NATIVE_CHROME === "1"
      ? [
          {
            name: "native-webmcp",
            use: {
              ...devices["Desktop Chrome"],
              channel: "chrome",
              launchOptions: { args: ["--enable-blink-features=WebMCP"] },
            },
          },
        ]
      : []),
  ],
  webServer: [
    {
      command:
        "cd ../.. && uv run --project apps/api python apps/api/tests/browser_server.py",
      url: "http://127.0.0.1:8108/health/live",
      reuseExistingServer: false,
    },
    {
      command:
        "corepack pnpm exec next dev --webpack --hostname 127.0.0.1 --port 3108",
      url: "http://127.0.0.1:3108",
      reuseExistingServer: false,
      env: {
        APP_BASE_URL: "http://127.0.0.1:3108",
        API_INTERNAL_URL: "http://127.0.0.1:8108",
        INCIDENT_OPS_HUMAN_ACCESS_TOKEN: "human-demo",
      },
    },
  ],
});
