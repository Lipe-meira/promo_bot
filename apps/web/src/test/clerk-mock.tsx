import type { ReactNode } from "react";

/** Controllable stand-in for Clerk's React surface; only the identity boundary is faked. */
export const clerk = {
  isLoaded: true,
  userId: "user_a" as string | null,
  getToken: async (): Promise<string | null> => (clerk.userId ? `token-for-${clerk.userId}` : null),
};

export function resetClerk() {
  clerk.isLoaded = true;
  clerk.userId = "user_a";
  clerk.getToken = async () => (clerk.userId ? `token-for-${clerk.userId}` : null);
}

export const useAuth = () => ({
  isLoaded: clerk.isLoaded,
  userId: clerk.userId,
  getToken: () => clerk.getToken(),
});

export const UserButton = () => <div data-testid="user-button" />;

export const SignOutButton = ({ children }: { children: ReactNode }) => <>{children}</>;
