const localFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function utcLabel(value: string): string {
  return new Date(value)
    .toISOString()
    .replace("T", " ")
    .replace(".000Z", " UTC");
}

export function Timestamp({
  value,
  exact = false,
}: {
  value: string;
  exact?: boolean;
}) {
  const date = new Date(value);
  const utc = utcLabel(value);
  return (
    <time className="timestamp" dateTime={value} title={utc}>
      <span>{localFormatter.format(date)}</span>
      {exact ? <span className="timestamp-utc">{utc}</span> : null}
    </time>
  );
}
