/**
 * The user a session token was issued for (its `sub` claim), or null if it cannot be read.
 *
 * This is a consistency check, not authentication: the signature is NOT verified here (only the API
 * can, and does). It exists so that an operation begun for one user is never sent with a token
 * that belongs to another, whatever the timing of the sign-in change.
 */
export function tokenSubject(token: string): string | null {
  const parts = token.split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const claims: unknown = JSON.parse(new TextDecoder().decode(bytes));
    const subject = (claims as { sub?: unknown } | null)?.sub;
    return typeof subject === "string" && subject.length > 0 ? subject : null;
  } catch {
    return null;
  }
}
