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
    ...(options.fetch ? { fetch: options.fetch } : {}),
    ...(options.headers ? { headers: options.headers } : {}),
  });
}

export type IncidentOpsClient = ReturnType<typeof createIncidentOpsClient>;
