import { LoadingLedger } from "@/components/ui/error-panel";

export default function IncidentsLoading() {
  return (
    <div className="page-stack">
      <header className="page-heading">
        <p className="eyebrow">Incident ledger</p>
        <h1>Active incidents</h1>
      </header>
      <LoadingLedger />
    </div>
  );
}
