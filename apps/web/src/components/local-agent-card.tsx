"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError, NETWORK_ERROR } from "@/lib/api/client";
import { describeError } from "@/lib/api/messages";
import type { AgentStatus, PairingCode } from "@/lib/api/types";
import { apiBaseUrl } from "@/lib/config";
import { useSession } from "@/lib/session";

import { ErrorView, LoadingView } from "./states";

const POLL_MS = 3000;
const TIMEOUT_MS = 10_000;

/** The answer, or a network error if none comes: silence must not leave the screen as it was. */
function answerWithin<T>(request: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new ApiClientError(0, NETWORK_ERROR, "No answer in time.")),
      ms,
    );
    request.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}

const PROCESS_LABELS: Record<AgentStatus["process_state"], string> = {
  running: "em execução",
  stopped: "parado",
  unknown: "desconhecido",
};

const EVENT_TEXT = {
  start_failed: "O agente não conseguiu iniciar o processo.",
  exited: "O processo terminou sozinho e não foi reiniciado.",
} as const;

/**
 * Start and stop a harmless test process on the owner's PC through the paired agent.
 *
 * The screen shows only what the API last reported. It polls while the page is visible, keeps one
 * change in flight at a time, ignores answers older than the one on screen, and, when a read fails
 * or is not answered in time, says that the state is unknown instead of keeping the old one.
 */
export function LocalAgentCard({
  workspaceId,
  pollMs = POLL_MS,
  timeoutMs = TIMEOUT_MS,
}: {
  workspaceId: string;
  pollMs?: number;
  timeoutMs?: number;
}) {
  const { api } = useSession();
  const apiRef = useRef(api);
  apiRef.current = api;
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const issued = useRef(0);
  const applied = useRef(0);
  const wasPaired = useRef<boolean | null>(null);

  const show = useCallback((data: AgentStatus) => {
    // The code was used (the workspace went from no agent to paired): stop showing it.
    if (wasPaired.current === false && data.paired) setPairing(null);
    wasPaired.current = data.paired;
    setAgent(data);
    setReadError(null);
  }, []);

  const read = useCallback(async () => {
    const mine = ++issued.current;
    try {
      const data = await answerWithin(apiRef.current.getAgent(workspaceId), timeoutMs);
      if (mine > applied.current) {
        applied.current = mine;
        show(data);
      }
    } catch (error) {
      if (mine > applied.current) {
        applied.current = mine;
        setReadError(describeError(error));
      }
    }
  }, [workspaceId, show, timeoutMs]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (!inFlight.current && !document.hidden) await read();
      if (active) timer = setTimeout(tick, pollMs);
    };
    void tick();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [read, pollMs]);

  /** One change at a time; whatever was being read before it is now out of date. */
  async function change(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      setNotice(describeError(error));
      await read();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function answered(data: AgentStatus) {
    applied.current = ++issued.current;
    show(data);
  }

  const send = (action: "start" | "stop") =>
    change(async () =>
      answered(await answerWithin(api.sendAgentCommand(workspaceId, action), timeoutMs)),
    );
  const generate = () =>
    change(async () =>
      setPairing(await answerWithin(api.createPairingCode(workspaceId), timeoutMs)),
    );
  const remove = () =>
    change(async () => {
      answered(await answerWithin(api.removeAgent(workspaceId), timeoutMs));
      setPairing(null);
    });

  const live = agent !== null && readError === null && agent.connected;
  const processState = live ? agent.process_state : "unknown";
  const pending = live ? agent.pending_action : null;
  const canStart = live && !busy && (pending ? pending === "stop" : processState === "stopped");
  const canStop = live && !busy && (pending ? pending === "start" : processState === "running");

  return (
    <section className="card" aria-labelledby="local-agent-title">
      <h2 id="local-agent-title">Agente local (teste)</h2>
      <p className="muted">
        Inicia e para, no seu PC, um processo de teste que não faz nada. O bot real ainda não está
        ligado a estes botões, e nenhuma credencial sai do seu computador.
      </p>
      {agent === null && readError === null ? <LoadingView label="Consultando o agente…" /> : null}
      {agent === null && readError !== null ? (
        <ErrorView message={readError} onRetry={() => void read()} />
      ) : null}
      {agent !== null && readError !== null ? (
        <p className="form-error" role="alert">
          Não foi possível atualizar o estado: {readError}
        </p>
      ) : null}

      {agent !== null && !agent.paired ? <p>Nenhum agente pareado neste workspace.</p> : null}
      {agent?.paired ? (
        <>
          <p className={live ? "badge badge-on" : "badge badge-off"}>
            {readError !== null
              ? "Sem informação do agente"
              : agent.connected
                ? "Agente conectado"
                : "Agente desconectado"}
          </p>
          <p aria-live="polite">
            Processo de teste: <strong>{PROCESS_LABELS[processState]}</strong>
          </p>
          {pending ? (
            <p className="muted" role="status">
              {pending === "start" ? "Iniciando…" : "Parando…"} aguardando o agente.
            </p>
          ) : null}
          {live && agent.last_event ? (
            <p className="form-error">{EVENT_TEXT[agent.last_event]}</p>
          ) : null}
          {!live && readError === null ? (
            <p className="muted">
              {agent.last_seen_at
                ? `Último contato: ${new Date(agent.last_seen_at).toLocaleString("pt-BR")}. `
                : "O agente não está em contato. "}
              No PC, execute <code>uv run promo-agent run</code>.
            </p>
          ) : null}
          <div className="button-row">
            <button type="button" className="button" disabled={!canStart} onClick={() => void send("start")}>
              Iniciar processo de teste
            </button>
            <button
              type="button"
              className="button button-secondary"
              disabled={!canStop}
              onClick={() => void send("stop")}
            >
              Parar processo de teste
            </button>
          </div>
        </>
      ) : null}

      {agent !== null ? (
        <div className="button-row">
          <button type="button" className="button button-secondary" disabled={busy} onClick={() => void generate()}>
            {agent.paired ? "Parear outro PC" : "Gerar código de pareamento"}
          </button>
          {agent.paired ? (
            <button type="button" className="button button-secondary" disabled={busy} onClick={() => void remove()}>
              Remover agente
            </button>
          ) : null}
        </div>
      ) : null}

      {pairing !== null ? (
        <div className="pairing">
          <p>
            Código de pareamento: <code className="pairing-code">{pairing.code}</code>
          </p>
          <p className="muted">
            Válido até {new Date(pairing.expires_at).toLocaleTimeString("pt-BR")}, de uso único.
            Parear substitui o agente anterior.
          </p>
          <p className="muted">
            No PC, na pasta <code>apps\agent</code> do projeto, execute e digite o código quando for
            pedido:
          </p>
          <pre>
            <code>{`uv run promo-agent pair --api ${apiBaseUrl()}`}</code>
          </pre>
          <p className="muted">Depois, deixe o agente em execução:</p>
          <pre>
            <code>uv run promo-agent run</code>
          </pre>
          <button type="button" className="button button-secondary" onClick={() => setPairing(null)}>
            Ocultar código
          </button>
        </div>
      ) : null}

      {notice ? (
        <p className="form-error" role="alert">
          {notice}
        </p>
      ) : null}
    </section>
  );
}
