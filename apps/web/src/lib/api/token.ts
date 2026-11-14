/** Who and which Clerk session a token claims to belong to: its `sub` and `sid` claims. */
export interface TokenIdentity {
  readonly subject: string;
  readonly sessionId: string;
}

/**
 * The user (`sub`) and the Clerk session (`sid`, a standard claim of the session token) a token was
 * issued for, or null if either cannot be read as a non-empty string.
 *
 * This is a consistency check, not authentication: the signature is NOT verified here (only the API
 * can, and does). It exists so that an operation begun under one session is never sent with a token
 * that belongs to another user or to another session of the same user.
 */
export function tokenIdentity(token: string): TokenIdentity | null {
  const parts = token.split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const claims: unknown = JSON.parse(new TextDecoder().decode(bytes));
    const { sub, sid } = (claims ?? {}) as { sub?: unknown; sid?: unknown };
    if (typeof sub !== "string" || sub.length === 0) return null;
    if (typeof sid !== "string" || sid.length === 0) return null;
    return { subject: sub, sessionId: sid };
  } catch {
    return null;
  }
}
