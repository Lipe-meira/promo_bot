"use client";

import { useState } from "react";

import { ApiClientError } from "@/lib/api/client";
import { describeError } from "@/lib/api/messages";
import type { Workspace } from "@/lib/api/types";
import { useSession } from "@/lib/session";
import { useAsync } from "@/lib/use-async";

import { ErrorView, LoadingView } from "./states";
import { WorkspaceForm, type WorkspaceFormValues } from "./workspace-form";
import { useWorkspaceSummary } from "./workspace-shell";

type Notice = "saved" | "conflict" | null;

export function SettingsScreen() {
  const summary = useWorkspaceSummary();
  const { api, reloadMe } = useSession();
  const workspace = useAsync(() => api.getWorkspace(summary.id));
  const [notice, setNotice] = useState<Notice>(null);

  async function save(current: Workspace, values: WorkspaceFormValues) {
    setNotice(null);
    try {
      await api.updateWorkspace(current.id, {
        version: current.version,
        ...(values.name !== current.name ? { name: values.name } : {}),
        ...(values.timezone !== current.timezone ? { timezone: values.timezone } : {}),
      });
    } catch (error) {
      if (error instanceof ApiClientError && error.code === "WORKSPACE_VERSION_CONFLICT") {
        setNotice("conflict");
      }
      throw error;
    }
    setNotice("saved");
    workspace.reload(); // the screen then shows what the server really holds
    reloadMe(); // keeps the sidebar name in step
  }

  return (
    <>
      <header className="page-header">
        <h1>Configurações</h1>
        <p className="muted">Dados do workspace, salvos no servidor.</p>
      </header>
      {workspace.status === "loading" ? <LoadingView label="Carregando configurações…" /> : null}
      {workspace.status === "error" ? (
        <ErrorView message={describeError(workspace.error)} onRetry={workspace.reload} />
      ) : null}
      {workspace.status === "ready" ? (
        <section className="card">
          <WorkspaceForm
            // A new version remounts the form, so the fields always show the server's values.
            key={workspace.data.version}
            initial={{ name: workspace.data.name, timezone: workspace.data.timezone }}
            submitLabel="Salvar alterações"
            onSubmit={(values) => save(workspace.data, values)}
          />
          {notice === "saved" ? (
            <p className="form-success" role="status">
              Alterações salvas.
            </p>
          ) : null}
          {notice === "conflict" ? (
            <button
              type="button"
              className="button button-secondary"
              onClick={() => {
                setNotice(null);
                workspace.reload();
              }}
            >
              Recarregar versão atual
            </button>
          ) : null}
          <p className="muted meta">
            Versão {workspace.data.version} · atualizado em{" "}
            {new Date(workspace.data.updated_at).toLocaleString("pt-BR")}
          </p>
        </section>
      ) : null}
    </>
  );
}
