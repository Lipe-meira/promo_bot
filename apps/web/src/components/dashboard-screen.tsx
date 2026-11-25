"use client";

import type { Capability, ComponentStatus } from "@/lib/api/types";
import { describeError } from "@/lib/api/messages";
import { useSession } from "@/lib/session";
import { useAsync } from "@/lib/use-async";

import { LocalAgentCard } from "./local-agent-card";
import { EmptyView, ErrorView, LoadingView } from "./states";
import { useWorkspaceSummary } from "./workspace-shell";

const STATE_LABELS: Record<ComponentStatus["state"], string> = {
  not_provisioned: "Não provisionado",
  not_connected: "Não conectado",
  not_configured: "Não configurado",
};

const COMPONENTS = [
  ["worker", "Worker"],
  ["telegram", "Telegram"],
  ["aliexpress", "AliExpress"],
] as const;

export function DashboardScreen() {
  const workspace = useWorkspaceSummary();
  return (
    <>
      <header className="page-header">
        <h1>{workspace.name}</h1>
        <p className="muted">Estado real do seu workspace.</p>
      </header>
      <StatusSection workspaceId={workspace.id} />
      <LocalAgentCard workspaceId={workspace.id} />
      <CapabilitiesSection workspaceId={workspace.id} />
    </>
  );
}

function StatusSection({ workspaceId }: { workspaceId: string }) {
  const { api } = useSession();
  const status = useAsync(() => api.getStatus(workspaceId));

  return (
    <section className="card" aria-labelledby="status-title">
      <h2 id="status-title">Operação</h2>
      {status.status === "loading" ? <LoadingView label="Consultando o estado…" /> : null}
      {status.status === "error" ? (
        <ErrorView message={describeError(status.error)} onRetry={status.reload} />
      ) : null}
      {status.status === "ready" ? (
        <>
          <p className="badge badge-off">
            {status.data.operational ? "Operacional" : "Nada em operação"}
          </p>
          <ul className="status-list">
            {COMPONENTS.map(([key, label]) => {
              const component = status.data[key];
              return (
                <li key={key}>
                  <div>
                    <strong>{label}</strong>
                    <span className="muted"> — {STATE_LABELS[component.state]}</span>
                  </div>
                  <p className="muted">{component.reason}</p>
                  {component.last_heartbeat_at ? (
                    <p className="muted">
                      Último sinal: {new Date(component.last_heartbeat_at).toLocaleString("pt-BR")}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
    </section>
  );
}

function CapabilityItem({ item }: { item: Capability }) {
  return (
    <li className={item.available ? "capability" : "capability capability-off"}>
      <div className="capability-head">
        <strong>{item.title}</strong>
        <span className={item.available ? "badge badge-on" : "badge badge-off"}>
          {item.available ? "Disponível" : "Indisponível"}
        </span>
      </div>
      {item.reason ? <p className="muted">{item.reason}</p> : null}
    </li>
  );
}

function CapabilitiesSection({ workspaceId }: { workspaceId: string }) {
  const { api } = useSession();
  const capabilities = useAsync(() => api.getCapabilities(workspaceId));

  return (
    <section className="card" aria-labelledby="capabilities-title">
      <h2 id="capabilities-title">Recursos</h2>
      {capabilities.status === "loading" ? <LoadingView label="Carregando recursos…" /> : null}
      {capabilities.status === "error" ? (
        <ErrorView message={describeError(capabilities.error)} onRetry={capabilities.reload} />
      ) : null}
      {capabilities.status === "ready" && capabilities.data.capabilities.length === 0 ? (
        <EmptyView title="Nenhum recurso" message="O servidor não listou recursos." />
      ) : null}
      {capabilities.status === "ready" ? (
        <ul className="capability-list">
          {capabilities.data.capabilities.map((item) => (
            <CapabilityItem key={item.key} item={item} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
