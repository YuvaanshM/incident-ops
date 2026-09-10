import type {
  ActionStatus,
  IncidentSeverity,
  IncidentStatus,
} from "@/lib/api/types";

const severityLabels: Record<IncidentSeverity, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

const incidentStatusLabels: Record<IncidentStatus, string> = {
  open: "Open",
  investigating: "Investigating",
  resolved: "Resolved",
  closed: "Closed",
};

const actionStatusLabels: Record<ActionStatus, string> = {
  pending: "Pending",
  applied: "Applied",
  rejected: "Rejected",
  failed: "Failed",
  stale: "Stale",
};

export function SeverityBadge({ severity }: { severity: IncidentSeverity }) {
  return (
    <span className={`badge badge-severity severity-${severity}`}>
      <span aria-hidden="true" className="badge-mark" />
      {severityLabels[severity]}
    </span>
  );
}

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return (
    <span className={`badge status-${status}`}>
      <span aria-hidden="true" className="badge-mark" />
      {incidentStatusLabels[status]}
    </span>
  );
}

export function ActionStatusBadge({ status }: { status: ActionStatus }) {
  return (
    <span className={`badge action-${status}`}>
      <span aria-hidden="true" className="badge-mark" />
      {actionStatusLabels[status]}
    </span>
  );
}

export function VersionBadge({ version }: { version: number }) {
  return <span className="version-badge">v{version}</span>;
}
