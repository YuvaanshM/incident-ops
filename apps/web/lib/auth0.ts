import "server-only";

import { Auth0Client } from "@auth0/nextjs-auth0/server";

const requiredAuth0Settings = [
  "AUTH0_DOMAIN",
  "AUTH0_CLIENT_ID",
  "AUTH0_CLIENT_SECRET",
  "AUTH0_SECRET",
  "APP_BASE_URL",
  "AUTH0_AUDIENCE",
] as const;

export function hostedAuthConfigured(
  environment: NodeJS.ProcessEnv = process.env,
): boolean {
  return requiredAuth0Settings.every((name) => environment[name]?.trim());
}

const production = process.env.NODE_ENV === "production";

export const auth0 = hostedAuthConfigured()
  ? new Auth0Client({
      authorizationParameters: {
        audience: process.env.AUTH0_AUDIENCE as string,
        scope:
          "openid profile email offline_access incidents:read incidents:write actions:read actions:request actions:approve fixtures:write",
      },
      enableAccessTokenEndpoint: false,
      signInReturnToPath: "/incidents",
      session: {
        absoluteDuration: 60 * 60 * 12,
        inactivityDuration: 60 * 60,
        rolling: true,
        cookie: { sameSite: "lax", secure: production },
      },
      transactionCookie: { secure: production },
    })
  : null;
