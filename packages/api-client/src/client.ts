import createClient from "openapi-fetch";

import type { paths } from "./schema.js";

export interface IncidentOpsClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
  headers?: HeadersInit;
}

export function createIncidentOpsClient(options: IncidentOpsClientOptions) {
  return createClient<paths>({
    baseUrl: options.baseUrl,
    fetch: options.fetch,
    headers: options.headers,
  });
}

export type IncidentOpsClient = ReturnType<typeof createIncidentOpsClient>;
