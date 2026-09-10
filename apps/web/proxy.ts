import { type NextRequest, NextResponse } from "next/server";

import { auth0 } from "@/lib/auth0";

function createContentSecurityPolicy(nonce: string): string {
  const development = process.env.NODE_ENV !== "production";
  const scriptSources = development
    ? "'self' 'unsafe-inline' 'unsafe-eval'"
    : `'self' 'nonce-${nonce}' 'strict-dynamic'`;
  const styleSources = development
    ? "'self' 'unsafe-inline'"
    : `'self' 'nonce-${nonce}'`;
  const connectSources = development
    ? "'self' http://localhost:8000 http://127.0.0.1:8000 ws: wss:"
    : "'self'";

  return [
    "default-src 'self'",
    `script-src ${scriptSources}`,
    `style-src ${styleSources}`,
    "img-src 'self' blob: data:",
    "font-src 'self'",
    `connect-src ${connectSources}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    development ? "" : "upgrade-insecure-requests",
  ]
    .filter(Boolean)
    .join("; ");
}

function applySecurityHeaders(
  response: NextResponse,
  contentSecurityPolicy: string,
): NextResponse {
  response.headers.set("Content-Security-Policy", contentSecurityPolicy);
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=()",
  );
  response.headers.set("Cross-Origin-Opener-Policy", "same-origin");
  if (process.env.NODE_ENV === "production") {
    response.headers.set(
      "Strict-Transport-Security",
      "max-age=31536000; includeSubDomains",
    );
  }
  return response;
}

export async function proxy(request: NextRequest): Promise<NextResponse> {
  const nonce = btoa(crypto.randomUUID());
  const contentSecurityPolicy = createContentSecurityPolicy(nonce);
  request.headers.set("Content-Security-Policy", contentSecurityPolicy);
  request.headers.set("x-nonce", nonce);

  if (!auth0) {
    const response =
      process.env.NODE_ENV === "production"
        ? NextResponse.json(
            {
              error: {
                code: "authentication_unavailable",
                message:
                  "Human sign-in is not configured for this environment.",
              },
            },
            { status: 503 },
          )
        : NextResponse.next({ request: { headers: request.headers } });
    return applySecurityHeaders(response, contentSecurityPolicy);
  }

  const authResponse = await auth0.middleware(request);
  if (request.nextUrl.pathname.startsWith("/auth")) {
    return applySecurityHeaders(authResponse, contentSecurityPolicy);
  }

  const session = await auth0.getSession(request);
  if (!session) {
    const response = request.nextUrl.pathname.startsWith("/api/ui/")
      ? NextResponse.json(
          {
            error: {
              code: "authentication_required",
              message: "Sign in to continue.",
            },
          },
          { status: 401 },
        )
      : NextResponse.redirect(
          new URL(
            `/auth/login?returnTo=${encodeURIComponent(
              `${request.nextUrl.pathname}${request.nextUrl.search}`,
            )}`,
            request.url,
          ),
        );
    return applySecurityHeaders(response, contentSecurityPolicy);
  }

  return applySecurityHeaders(authResponse, contentSecurityPolicy);
}

export const config = {
  matcher: [
    {
      source:
        "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
