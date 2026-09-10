import { Timestamp } from "@/components/ui/timestamp";
import type { IncidentEvent } from "@/lib/api/types";

function humanize(value: string): string {
  return value
    .replaceAll(".", " ")
    .replaceAll("_", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function PayloadValue({ value }: { value: unknown }) {
  if (isRecord(value) && "from" in value && "to" in value) {
    return (
      <>
        {String(value.from ?? "—")} → {String(value.to ?? "—")}
      </>
    );
  }
  if (Array.isArray(value)) {
    return <>{value.map(String).join(", ") || "—"}</>;
  }
  if (isRecord(value)) {
    return <>{JSON.stringify(value)}</>;
  }
  return <>{value === null || value === undefined ? "—" : String(value)}</>;
}

function PayloadRows({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).flatMap(([key, value]) => {
    if (key === "changes" && isRecord(value)) {
      return Object.entries(value);
    }
    return [[key, value] as const];
  });
  if (entries.length === 0) return null;
  return (
    <dl className="event-payload">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{humanize(key)}</dt>
          <dd>
            <PayloadValue value={value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Timeline({ events }: { events: IncidentEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="timeline-empty">
        <h3>No events recorded yet</h3>
        <p>The immutable response history will appear here.</p>
      </div>
    );
  }

  return (
    <ol className="timeline-list">
      {events.map((event) => (
        <li className="timeline-event" key={event.id}>
          <span aria-hidden="true" className="timeline-node" />
          <div className="timeline-event-head">
            <div>
              <h3>{humanize(event.event_type)}</h3>
              <p>
                {event.actor_type} · {event.actor_id}
              </p>
            </div>
            <Timestamp exact value={event.occurred_at} />
          </div>
          <PayloadRows payload={event.payload} />
          <div className="event-references">
            <span>request {event.request_id.slice(0, 8)}</span>
            {event.action_request_id ? (
              <span>action {event.action_request_id.slice(0, 8)}</span>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
