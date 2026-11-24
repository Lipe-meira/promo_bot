/** Synthetic, unsigned session tokens: JWT-shaped so the client can read `sub`/`sid`, never verifiable. */

const segment = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** The Clerk session a user is signed in with unless a test says otherwise. */
export const defaultSessionOf = (userId: string) => `sess_${userId}`;

/**
 * Pass `{ sid: undefined }` to issue a token with no session claim, or any other value to forge one.
 * `sub` always wins over `claims`.
 */
export function tokenFor(userId: string, claims: Record<string, unknown> = {}): string {
  const header = segment({ alg: "RS256", typ: "JWT" });
  const payload = { sid: defaultSessionOf(userId), ...claims, sub: userId };
  return `${header}.${segment(payload)}.synthetic-signature`;
}

function claimsOf(header: string): { sub?: unknown; sid?: unknown } | null {
  const match = /^Bearer ([^.]+)\.([^.]+)\.[^.]+$/.exec(header);
  if (!match) return null;
  try {
    const padded = match[2]!.replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(padded)) as { sub?: unknown; sid?: unknown };
  } catch {
    return null;
  }
}

/** The user a `Bearer <token>` header was issued for, as the fake API sees it. */
export function userOfAuthorization(header: string): string | null {
  const sub = claimsOf(header)?.sub;
  return typeof sub === "string" ? sub : null;
}

/** The Clerk session a `Bearer <token>` header was issued for (`sid`), as the fake API sees it. */
export function sessionOfAuthorization(header: string): string | null {
  const sid = claimsOf(header)?.sid;
  return typeof sid === "string" ? sid : null;
}
