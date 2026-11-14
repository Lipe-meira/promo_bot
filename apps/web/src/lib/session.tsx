"use client";

import { useAuth } from "@clerk/nextjs";
import { createContext, useContext, useMemo, useRef, type ReactNode } from "react";

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
  return <SessionProvider key={userId}>{children}</SessionProvider>;
}

function SessionProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const api = useMemo(
    () => createApiClient(apiBaseUrl(), () => getTokenRef.current()),
    [],
  );
  const { reload, ...me } = useAsync(() => api.me());
  const value: SessionValue = { api, me, reloadMe: reload };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
