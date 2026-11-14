"use client";

import { useAuth, useClerk } from "@clerk/nextjs";
import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

import { createApiClient, type ApiClient } from "@/lib/api/client";
import type { Me } from "@/lib/api/types";
import { apiBaseUrl } from "@/lib/config";
import { useAsync, type AsyncState } from "@/lib/use-async";

import { LoadingView } from "@/components/states";

interface SessionValue {
  api: ApiClient;
  me: AsyncState<Me>;
  reloadMe: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionGate>");
  return value;
}

/**
 * Binds the API client to the signed-in Clerk *session*. The inner provider is keyed by user id and
 * session id, so when the account — or just the session of the same account — changes, every piece
 * of state (profile, workspaces, forms) is discarded and one session's data can never be shown to,
 * or sent under, the next.
 *
 * The key only governs what React renders. It is not what stops an operation that is already in
 * flight: that is the provider's own lifetime and the client's pre-send check (see below).
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const { isLoaded, userId, sessionId } = useAuth();
  if (!isLoaded) return <LoadingView label="Verificando sua sessão…" />;
  if (!userId || !sessionId) return <LoadingView label="Redirecionando para o login…" />;
  return (
    <SessionProvider key={`${userId}:${sessionId}`} userId={userId} sessionId={sessionId}>
      {children}
    </SessionProvider>
  );
}

/** Fail-closed stand-in used before the provider has mounted: any operation on it is dropped. */
const ENDED = AbortSignal.abort();

type ClerkState = Pick<ReturnType<typeof useClerk>, "loaded" | "session" | "user">;
type Emitted = { session?: ClerkState["session"]; user?: ClerkState["user"] };

/** Is `session`/`user` exactly the active session this provider was rendered for? */
function isBoundSession(
  { session, user }: Emitted,
  userId: string,
  sessionId: string,
): boolean {
  return session?.status === "active" && session.id === sessionId && user?.id === userId;
}

function SessionProvider({
  userId,
  sessionId,
  children,
}: {
  userId: string;
  sessionId: string;
  children: ReactNode;
}) {
  const clerk = useClerk();
  const { getToken } = useAuth();
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  // Live read of the effective Clerk state: it reflects a sign-out or a session replacement the
  // moment Clerk applies it, before React has re-rendered anything. Documented Clerk properties.
  // An *active* session of the original user with the original id already implies "signed in".
  const isActive = useCallback(
    () => clerk.loaded && isBoundSession(clerk, userId, sessionId),
    [clerk, userId, sessionId],
  );

  // The lifetime of this provider instance. Everything that starts under it (profile load, form
  // submissions) is bound to this signal. It ends when the provider unmounts *and* — without waiting
  // for React — as soon as Clerk reports that the session this provider was rendered for is no
  // longer the active one (sign-out, replacement by another session or user, revocation, expiry),
  // aborting the in-flight requests. Created in a layout effect, which runs before any child effect
  // issues a request, and recreated on a StrictMode remount instead of being left aborted; the
  // listener is removed by the same cleanup, so remounts never leave a second one behind. If Clerk
  // had already moved on before this effect ran, `isActive()` refuses every operation anyway.
  const lifetime = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    // `undefined` means Clerk is (re)loading its resources: no decision either way.
    const unsubscribe = clerk.addListener(
      (emission) => {
        if (emission.session === undefined) return;
        if (!isBoundSession(emission, userId, sessionId)) controller.abort();
      },
      { skipInitialEmit: true },
    );
    return () => {
      unsubscribe();
      controller.abort();
    };
  }, [clerk, userId, sessionId]);

  const api = useMemo(
    () =>
      createApiClient(apiBaseUrl(), {
        userId,
        sessionId,
        getToken: () => getTokenRef.current(),
        signal: () => lifetime.current?.signal ?? ENDED,
        isActive,
      }),
    [userId, sessionId, isActive],
  );
  const { reload, ...me } = useAsync(() => api.me());
  const value: SessionValue = { api, me, reloadMe: reload };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
