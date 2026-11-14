import type { ReactNode } from "react";

import { tokenFor } from "./tokens";

/**
 * Controllable stand-in for Clerk's React surface; only the identity boundary is faked.
 * Like the real `getToken`, the default answers for the session that is active *when it settles*.
 */
export const clerk = {
  isLoaded: true,
  userId: "user_a" as string | null,
  getToken: async (): Promise<string | null> => (clerk.userId ? tokenFor(clerk.userId) : null),
};

export function resetClerk() {
  clerk.isLoaded = true;
  clerk.userId = "user_a";
  releaseTokens();
}

export interface PendingToken {
  /** Settles with the token of whoever is signed in right now (what Clerk does). */
  resolveForCurrentSession(): void;
  /** Settles with a token issued for a specific user, whoever is signed in now. */
  resolveFor(userId: string): void;
  resolveNull(): void;
}

/** Makes every `getToken()` call wait until the test settles it; returns the calls in order. */
export function holdTokens(): PendingToken[] {
  const pending: PendingToken[] = [];
  clerk.getToken = () =>
    new Promise<string | null>((resolve) => {
      pending.push({
        resolveForCurrentSession: () => resolve(clerk.userId ? tokenFor(clerk.userId) : null),
        resolveFor: (userId) => resolve(tokenFor(userId)),
        resolveNull: () => resolve(null),
      });
    });
  return pending;
}

/** Ends a `holdTokens()` hold: later calls settle immediately again. */
export function releaseTokens() {
  clerk.getToken = async () => (clerk.userId ? tokenFor(clerk.userId) : null);
}

export const useAuth = () => ({
  isLoaded: clerk.isLoaded,
  userId: clerk.userId,
  getToken: () => clerk.getToken(),
});

export const UserButton = () => <div data-testid="user-button" />;

export const SignOutButton = ({ children }: { children: ReactNode }) => <>{children}</>;
