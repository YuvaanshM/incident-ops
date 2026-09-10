import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { auth0 } from "@/lib/auth0";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Incident Ops",
    template: "%s · Incident Ops",
  },
  description: "Human-agent incident response control plane",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AppShell hostedAuthEnabled={auth0 !== null}>{children}</AppShell>
      </body>
    </html>
  );
}
