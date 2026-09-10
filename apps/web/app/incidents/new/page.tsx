import type { Metadata } from "next";
import Link from "next/link";

import { IncidentForm } from "@/components/incidents/incident-form";

export const metadata: Metadata = { title: "New incident" };

export default function NewIncidentPage() {
  return (
    <div className="page-stack page-narrow">
      <nav aria-label="Breadcrumb" className="breadcrumbs">
        <Link href="/incidents">Incidents</Link>
        <span aria-hidden="true">/</span>
        <span>New</span>
      </nav>
      <header className="page-heading">
        <p className="eyebrow">Open the record</p>
        <h1>Create an incident</h1>
        <p className="page-intro">
          Start with what is known. Every later change is added to the immutable
          audit log.
        </p>
      </header>
      <section
        aria-labelledby="incident-details-heading"
        className="work-surface"
      >
        <div aria-hidden="true" className="section-number">
          01
        </div>
        <div className="work-surface-body">
          <h2 id="incident-details-heading">Initial details</h2>
          <IncidentForm />
        </div>
      </section>
    </div>
  );
}
