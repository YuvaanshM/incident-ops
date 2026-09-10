import type { ApiError } from "@/lib/api/errors";

export function ErrorPanel({
  error,
  onRetry,
  title = "We could not load this ledger",
}: {
  error: ApiError;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <section className="notice notice-error" role="alert">
      <p className="notice-kicker">{error.code.replaceAll("_", " ")}</p>
      <h2>{title}</h2>
      <p>{error.message}</p>
      {error.requestId ? (
        <p className="request-id">Request {error.requestId}</p>
      ) : null}
      {onRetry ? (
        <button
          className="button button-secondary"
          onClick={onRetry}
          type="button"
        >
          Try again
        </button>
      ) : null}
    </section>
  );
}

export function LoadingLedger({
  label = "Loading incidents",
}: {
  label?: string;
}) {
  return (
    <div
      aria-label={label}
      aria-live="polite"
      className="ledger-skeleton"
      role="status"
    >
      <span className="sr-only">{label}</span>
      {[0, 1, 2].map((row) => (
        <div aria-hidden="true" className="skeleton-row" key={row}>
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}
