"use client";

import { useAuth } from "@clerk/nextjs";
import {
  createContext,
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
 * Binds the API client to the signed-in Clerk user. The inner provider is keyed by the user id,
 * so when the account changes every piece of state (profile, workspaces, forms) is discarded and
 * one user's data can never be shown to the next.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const { isLoaded, userId } = useAuth();
  if (!isLoaded) return <LoadingView label="Verificando sua sessão…" />;
  if (!userId) return <LoadingView label="Redirecionando para o login…" />;
  return (
    <SessionProvider key={userId} userId={userId}>
      {children}
    </SessionProvider>
  );
}

/** Fail-closed stand-in used before the provider has mounted: any operation on it is dropped. */
const ENDED = AbortSignal.abort();

function SessionProvider({ userId, children }: { userId: string; children: ReactNode }) {
  const { getToken } = useAuth();
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  // The lifetime of this provider instance. Everything that starts under it (profile load, form
  // submissions) is bound to this signal, and unmounting — sign-out, account switch, navigation
  // away — aborts it, so no operation of this session can outlive it or be sent with another
  // identity. Created in a layout effect, which runs before any child effect issues a request, and
  // recreated on a StrictMode remount instead of being left aborted.
  const lifetime = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);

  const api = useMemo(
    () =>
      createApiClient(apiBaseUrl(), {
        userId,
        getToken: () => getTokenRef.current(),
        signal: () => lifetime.current?.signal ?? ENDED,
      }),
    [userId],
  );
  const { reload, ...me } = useAsync(() => api.me());
  const value: SessionValue = { api, me, reloadMe: reload };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
