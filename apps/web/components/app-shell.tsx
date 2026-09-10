"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/incidents", label: "Incidents", marker: "01" },
  { href: "/approvals", label: "Approvals", marker: "02" },
];

export function AppShell({
  children,
  hostedAuthEnabled,
}: {
  children: React.ReactNode;
  hostedAuthEnabled: boolean;
}) {
  const pathname = usePathname();
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <aside className="sidebar">
        <Link
          aria-label="Incident Ops incidents"
          className="brand"
          href="/incidents"
        >
          <span aria-hidden="true" className="brand-mark">
            <i />
            <i />
            <i />
          </span>
          <span>
            Incident
            <br />
            Ops
          </span>
        </Link>
        <nav aria-label="Primary navigation" className="primary-nav">
          {navigation.map((item) => {
            const active =
              pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                aria-current={active ? "page" : undefined}
                href={item.href}
                key={item.href}
              >
                <span>{item.marker}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="operator-stamp">
          <span aria-hidden="true" className="operator-pulse" />
          <div>
            <strong>Human operator</strong>
            <span>Control plane</span>
          </div>
          {hostedAuthEnabled ? (
            <a className="sign-out-link" href="/auth/logout">
              Sign out
            </a>
          ) : null}
        </div>
      </aside>
      <header className="mobile-header">
        <Link className="mobile-brand" href="/incidents">
          Incident Ops
        </Link>
        <nav aria-label="Primary navigation">
          {navigation.map((item) => (
            <Link
              aria-current={
                pathname === item.href || pathname.startsWith(`${item.href}/`)
                  ? "page"
                  : undefined
              }
              href={item.href}
              key={item.href}
            >
              {item.label}
            </Link>
          ))}
          {hostedAuthEnabled ? <a href="/auth/logout">Sign out</a> : null}
        </nav>
      </header>
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
