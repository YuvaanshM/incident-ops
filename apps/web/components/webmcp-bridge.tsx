"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { UUID_PATTERN } from "@/lib/webmcp/contracts";
import {
  recoverToolRequest,
  registerIncidentTools,
  type ModelContext,
  type ToolResult,
} from "@/lib/webmcp/tools";

export function WebMcpBridge() {
  const pathname = usePathname();
  const [result, setResult] = useState<ToolResult | null>(null);
  const [checking, setChecking] = useState(false);
  const recovery = useRef<AbortController | null>(null);
  useEffect(() => {
    const context = (document as Document & { modelContext?: ModelContext })
      .modelContext;
    if (
      !globalThis.isSecureContext ||
      typeof context?.registerTool !== "function"
    )
      return;
    return registerIncidentTools(context, setResult);
  }, [pathname]);
  useEffect(() => () => recovery.current?.abort(), []);
  if (!result) return null;
  const unknown = result.request?.commit_status === "unknown";
  const data = result.data;
  const actionId =
    data &&
    typeof data === "object" &&
    "id" in data &&
    typeof data.id === "string" &&
    UUID_PATTERN.test(data.id)
      ? data.id
      : null;
  async function checkResult() {
    if (!result?.request || checking) return;
    recovery.current = new AbortController();
    setChecking(true);
    const next = await recoverToolRequest(
      result.request.idempotency_key,
      recovery.current.signal,
    );
    if (!recovery.current.signal.aborted) {
      setResult(next);
      setChecking(false);
    }
  }
  return (
    <aside
      aria-label="Browser agent result"
      className="agent-result"
      role="status"
    >
      <strong>Agent request: {result.outcome.replaceAll("_", " ")}</strong>
      {unknown ? (
        <>
          <p>
            The request may still complete. Check its result before submitting
            another request.
          </p>
          <button
            className="button button-secondary"
            disabled={checking}
            onClick={() => void checkResult()}
          >
            {checking ? "Checking…" : "Check request result"}
          </button>
        </>
      ) : null}
      {actionId ? (
        <Link href={`/approvals/${actionId}`}>Review request</Link>
      ) : null}
      <button className="button button-quiet" onClick={() => setResult(null)}>
        Dismiss
      </button>
    </aside>
  );
}
