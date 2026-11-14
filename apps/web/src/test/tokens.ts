/** Synthetic, unsigned session tokens: JWT-shaped so the client can read `sub`, never verifiable. */

const segment = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export function tokenFor(userId: string, claims: Record<string, unknown> = {}): string {
  const header = segment({ alg: "RS256", typ: "JWT" });
  return `${header}.${segment({ ...claims, sub: userId })}.synthetic-signature`;
}

/** The user a `Bearer <token>` header was issued for, as the fake API sees it. */
export function userOfAuthorization(header: string): string | null {
  const match = /^Bearer ([^.]+)\.([^.]+)\.[^.]+$/.exec(header);
  if (!match) return null;
  try {
    const padded = match[2]!.replace(/-/g, "+").replace(/_/g, "/");
    const claims = JSON.parse(atob(padded)) as { sub?: unknown };
    return typeof claims.sub === "string" ? claims.sub : null;
  } catch {
    return null;
  }
}
