import { LoadingLedger } from "@/components/ui/error-panel";

export default function Loading() {
  return (
    <div className="page-stack">
      <LoadingLedger label="Loading approval desk" />
    </div>
  );
}
