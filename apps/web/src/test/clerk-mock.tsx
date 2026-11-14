import type { ReactNode } from "react";

import { defaultSessionOf, tokenFor } from "./tokens";

/**
 * Controllable stand-in for Clerk's React surface; only the identity boundary is faked.
 *
 * Two things are kept apart on purpose, because the bug class under test lives in the gap between
 * them:
 *
 * - the **effective Clerk state** (`clerk.userId`, `clerk.sessionId`, `clerk.sessionStatus`,
 *   `clerk.isLoaded`): what the Clerk object itself reports *right now*, changed synchronously by
 *   `signOutNow` / `replaceSession` / `signInAs`, exactly as clerk-js does;
 * - the **React rendering**: `useAuth()` returns what the live state says *when a component renders*.
 *   Nothing here re-renders anything: a test that wants React to catch up calls `view.rerender()`.
 *   Until then the mounted `SessionGate` keeps the identity it last rendered.
 *
 * Like the real `getToken`, the default answers for the session that is active *when it settles*.
 */
export type SessionStatus = "active" | "ended" | "replaced" | "revoked" | "expired" | "pending";

export interface Emission {
  session: { id: string; status: SessionStatus; user: { id: string } } | null | undefined;
  user: { id: string } | null | undefined;
}

export const clerk = {
  isLoaded: true,
  userId: "user_a" as string | null,
  /** Overrides the session derived from `userId`; null means "the user's default session". */
  sessionIdOverride: null as string | null,
  sessionStatus: "active" as SessionStatus,
  /** When false, state changes are NOT announced to listeners (isolates the pre-send check). */
  notify: true,
  get sessionId(): string | null {
    return clerk.userId ? (clerk.sessionIdOverride ?? defaultSessionOf(clerk.userId)) : null;
  },
  getToken: async (): Promise<string | null> => currentToken(),
};

const listeners = new Set<(emission: Emission) => void>();
/** Every `addListener` ever made and whether it was unsubscribed (leak checks). */
export const listenerLog = { added: 0, removed: 0 };

export const listenerCount = () => listeners.size;

function currentToken(): string | null {
  return clerk.userId ? tokenFor(clerk.userId, { sid: clerk.sessionId }) : null;
}

function snapshot(): Emission {
  if (!clerk.userId || !clerk.sessionId) return { session: null, user: null };
  const user = { id: clerk.userId };
  return { session: { id: clerk.sessionId, status: clerk.sessionStatus, user }, user };
}

function emit() {
  if (!clerk.notify) return;
  for (const listener of [...listeners]) listener(snapshot());
}

/** The Clerk session ends (sign-out). React is NOT told: no re-render happens. */
export function signOutNow() {
  clerk.sessionStatus = "ended";
  clerk.userId = null;
  clerk.sessionIdOverride = null;
  emit();
}

/** Clerk replaces the session by another one (any user). React is NOT told. */
export function replaceSession(userId: string, sessionId?: string) {
  clerk.userId = userId;
  clerk.sessionIdOverride = sessionId ?? null;
  clerk.sessionStatus = "active";
  emit();
}

/** The session stays but stops being active (revoked, expired, pending a task...). */
export function setSessionStatus(status: SessionStatus) {
  clerk.sessionStatus = status;
  emit();
}

/** The Clerk object reports "loading" (session undefined) for a moment. */
export function emitLoading() {
  for (const listener of [...listeners]) listener({ session: undefined, user: undefined });
}

export function resetClerk() {
  clerk.isLoaded = true;
  clerk.userId = "user_a";
  clerk.sessionIdOverride = null;
  clerk.sessionStatus = "active";
  clerk.notify = true;
  listeners.clear();
  listenerLog.added = 0;
  listenerLog.removed = 0;
  releaseTokens();
}

export interface PendingToken {
  /** Settles with the token of whoever is signed in right now (what Clerk does). */
  resolveForCurrentSession(): void;
  /** Settles with a token issued for a user (and optionally a session), whoever is signed in now. */
  resolveFor(userId: string, sessionId?: string): void;
  /** Settles with a token carrying exactly these claims (after `sub`). */
  resolveWith(userId: string, claims: Record<string, unknown>): void;
  /** Settles with an arbitrary string, e.g. something that is not a JWT. */
  resolveRaw(token: string): void;
  resolveNull(): void;
}

/** Makes every `getToken()` call wait until the test settles it; returns the calls in order. */
export function holdTokens(): PendingToken[] {
  const pending: PendingToken[] = [];
  clerk.getToken = () =>
    new Promise<string | null>((resolve) => {
      pending.push({
        resolveForCurrentSession: () => resolve(currentToken()),
        resolveFor: (userId, sessionId) =>
          resolve(tokenFor(userId, sessionId ? { sid: sessionId } : {})),
        resolveWith: (userId, claims) => resolve(tokenFor(userId, claims)),
        resolveRaw: (token) => resolve(token),
        resolveNull: () => resolve(null),
      });
    });
  return pending;
}

/** Ends a `holdTokens()` hold: later calls settle immediately again. */
export function releaseTokens() {
  clerk.getToken = async () => currentToken();
}

export const useAuth = () => ({
  isLoaded: clerk.isLoaded,
  userId: clerk.userId,
  sessionId: clerk.sessionId,
  isSignedIn: Boolean(clerk.userId),
  getToken: () => clerk.getToken(),
});

/** The (stable) Clerk object: live reads and a listener registry, as clerk-js exposes them. */
const clerkObject = {
  get loaded() {
    return clerk.isLoaded;
  },
  get isSignedIn() {
    return Boolean(clerk.userId);
  },
  get session() {
    return snapshot().session;
  },
  get user() {
    return snapshot().user;
  },
  addListener(callback: (emission: Emission) => void, options?: { skipInitialEmit?: boolean }) {
    listeners.add(callback);
    listenerLog.added += 1;
    if (!options?.skipInitialEmit) callback(snapshot());
    let done = false;
    return () => {
      if (done) return;
      done = true;
      listeners.delete(callback);
      listenerLog.removed += 1;
    };
  },
};

export const useClerk = () => clerkObject;

export const UserButton = () => <div data-testid="user-button" />;

export const SignOutButton = ({ children }: { children: ReactNode }) => <>{children}</>;
