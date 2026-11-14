"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ApiClientError } from "@/lib/api/client";
import { useSession } from "@/lib/session";

import { MeBoundary } from "./me-boundary";
import { LoadingView } from "./states";
import { WorkspaceForm } from "./workspace-form";

export function OnboardingScreen() {
  return <MeBoundary>{(me) => <Onboarding hasWorkspace={me.workspaces.length > 0} />}</MeBoundary>;
}

function Onboarding({ hasWorkspace }: { hasWorkspace: boolean }) {
  const router = useRouter();
  const { api, reloadMe } = useSession();

  useEffect(() => {
    if (hasWorkspace) router.replace("/dashboard");
  }, [hasWorkspace, router]);

  if (hasWorkspace) return <LoadingView label="Abrindo seu dashboard…" />;

  async function create(values: { name: string; timezone: string }) {
    try {
      await api.createWorkspace(values);
    } catch (error) {
      // Already has one (repeated or concurrent creation): the outcome is the one wanted.
      if (!(error instanceof ApiClientError && error.code === "WORKSPACE_ALREADY_EXISTS")) {
        throw error;
      }
    }
    reloadMe();
  }

  return (
    <div className="centered">
      <section className="card onboarding">
        <h1>Crie seu workspace</h1>
        <p className="muted">
          É o espaço onde ficam as configurações da sua operação. Você poderá alterar estes dados
          depois.
        </p>
        <WorkspaceForm
          initial={{ name: "", timezone: "America/Sao_Paulo" }}
          submitLabel="Criar workspace"
          onSubmit={create}
        />
      </section>
    </div>
  );
}
