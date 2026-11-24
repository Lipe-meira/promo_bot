"use client";

import { SignOutButton } from "@clerk/nextjs";
import type { ReactNode } from "react";

import { ApiClientError } from "@/lib/api/client";
import { admissionReason, describeError } from "@/lib/api/messages";
import type { Me } from "@/lib/api/types";
import { useSession } from "@/lib/session";

import { ErrorView, LoadingView } from "./states";

const SESSION_CODES = new Set(["AUTH_INVALID_TOKEN", "AUTH_TOKEN_EXPIRED"]);

function SignOut() {
  return (
    <SignOutButton redirectUrl="/sign-in">
      <button type="button" className="button button-secondary">
        Sair
      </button>
    </SignOutButton>
  );
}

/** Resolves the signed-in person's local profile and renders `children(me)` only when admitted. */
export function MeBoundary({ children }: { children: (me: Me) => ReactNode }) {
  const { me, reloadMe } = useSession();

  if (me.status === "loading") return <LoadingView label="Carregando sua conta…" />;
  if (me.status === "ready") return <>{children(me.data)}</>;

  const error = me.error;
  if (error instanceof ApiClientError && error.code === "NOT_ADMITTED") {
    return (
      <ErrorView title="Acesso restrito" message={describeError(error)}>
        <p className="muted">{admissionReason(error) ?? "Peça um convite ao administrador."}</p>
        <SignOut />
      </ErrorView>
    );
  }
  if (error instanceof ApiClientError && SESSION_CODES.has(error.code)) {
    return (
      <ErrorView title="Sessão encerrada" message={describeError(error)}>
        <SignOut />
      </ErrorView>
    );
  }
  return (
    <ErrorView
      title="Não foi possível carregar sua conta"
      message={describeError(error)}
      onRetry={reloadMe}
    />
  );
}
