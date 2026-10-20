"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { WebMcpBridge } from "@/components/webmcp-bridge";

const navigation = [
  { href: "/incidents", label: "Incidents", icon: "M3 12h4l3-8 4 16 3-8h4" },
  {
    href: "/approvals",
    label: "Approvals",
    icon: "M9 12l2 2 4-4 M12 3l8 3v6c0 4-8 9-8 9s-8-5-8-9V6z",
  },
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
                <svg
                  aria-hidden="true"
                  className="nav-icon"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d={item.icon} />
                </svg>
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
        <WebMcpBridge />
        {children}
      </main>
    </div>
  );
}
