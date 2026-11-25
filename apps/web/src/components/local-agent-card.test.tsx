import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SessionGate } from "@/lib/session";
import { errorReply, FakeApi } from "@/test/fake-api";
import { navigation } from "@/test/navigation-mock";

import { LocalAgentCard } from "./local-agent-card";

let api: FakeApi;
let workspaceId: string;

beforeEach(() => {
  api = new FakeApi().install();
  navigation.pathname = "/dashboard";
  workspaceId = api.seedWorkspace("user_a", "Loja da Alice").id;
});

afterEach(() => setHidden(false));

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
}

function card(pollMs = 20, timeoutMs?: number) {
  return render(
    <SessionGate>
      <LocalAgentCard workspaceId={workspaceId} pollMs={pollMs} timeoutMs={timeoutMs} />
    </SessionGate>,
  );
}

/** A request that arrives and is never answered. */
const never = () => new Promise<void>(() => {});

const start = () => screen.getByRole("button", { name: "Iniciar processo de teste" });
const stop = () => screen.getByRole("button", { name: "Parar processo de teste" });
const sent = () => api.calls.filter((call) => call.path.endsWith("/agent/commands"));

describe("local agent card", () => {
  it("offers pairing when there is no agent and shows the code with the PC commands", async () => {
    card();
    expect(await screen.findByText("Nenhum agente pareado neste workspace.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Iniciar processo de teste" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Gerar código de pareamento" }));

    expect(await screen.findByText("ABCD-EFGH-JKMN")).toBeVisible();
    expect(screen.getByText("uv run promo-agent pair --api http://api.test")).toBeVisible();
    expect(screen.getByText("uv run promo-agent run")).toBeVisible();
    expect(screen.getByText(/uso único/)).toBeVisible();

    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    expect(await screen.findByText("Agente conectado")).toBeVisible();
    expect(screen.queryByText("ABCD-EFGH-JKMN")).toBeNull(); // used: no longer shown
  });

  it("says the agent is disconnected, the state unknown, and takes no command", async () => {
    api.setAgent(workspaceId, {
      paired: true,
      connected: false,
      process_state: "unknown",
      last_seen_at: "2026-11-25T12:00:00Z",
    });
    card();

    expect(await screen.findByText("Agente desconectado")).toBeVisible();
    expect(screen.getByText("desconhecido")).toBeVisible();
    expect(screen.getByText(/Último contato/)).toBeVisible();
    expect(start()).toBeDisabled();
    expect(stop()).toBeDisabled();
  });

  it("follows start and stop through to what the agent reports", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    card();
    expect(await screen.findByText("parado")).toBeVisible();
    expect(stop()).toBeDisabled();

    await userEvent.click(start());

    expect(await screen.findByText(/Iniciando/)).toBeVisible();
    expect(screen.getByText("parado")).toBeVisible(); // asking is not running
    expect(start()).toBeDisabled(); // no second request while one is pending

    api.setAgent(workspaceId, { process_state: "running", pending_action: null });
    expect(await screen.findByText("em execução")).toBeVisible();
    expect(screen.queryByText(/Iniciando/)).toBeNull();
    expect(start()).toBeDisabled();

    await userEvent.click(stop());
    expect(await screen.findByText(/Parando/)).toBeVisible();
    api.setAgent(workspaceId, { process_state: "stopped", pending_action: null });
    expect(await screen.findByText("parado")).toBeVisible();

    expect(sent().map((call) => call.body)).toEqual([{ action: "start" }, { action: "stop" }]);
  });

  it("sends one request for a double click", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    card(60_000);
    await screen.findByText("parado");
    api.delayMs = 30;

    await userEvent.dblClick(start());

    expect(await screen.findByText(/Iniciando/)).toBeVisible();
    expect(sent()).toHaveLength(1);
  });

  it("does not let an older poll undo the answer to a click", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let reads = 0;
    let heldAnswered = false;
    let staleAnswer: Promise<void> | null = null;
    api.beforeReply = async (request) => {
      if (request.method === "GET" && request.path.endsWith("/agent") && ++reads === 2) {
        staleAnswer = held;
        await held;
        heldAnswered = true;
      }
    };
    api.snapshotReads = true; // the held read answers with what was true when it arrived
    card(10);
    await screen.findByText("parado");
    await waitFor(() => expect(staleAnswer).not.toBeNull());

    await userEvent.click(start());
    expect(await screen.findByText(/Iniciando/)).toBeVisible();
    api.beforeReply = async (request) => {
      if (request.method === "GET") await new Promise(() => {}); // no newer poll from here on
    };
    expect(heldAnswered).toBe(false); // the older read is still in flight
    release();
    await waitFor(() => expect(heldAnswered).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.getByText(/Iniciando/)).toBeVisible();
  });

  it("does not present an old state as current when the API stops answering", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "running" });
    card();
    expect(await screen.findByText("em execução")).toBeVisible();

    api.failPath = { match: /\/agent$/, reply: errorReply(503, "DATABASE_UNAVAILABLE") };

    expect(await screen.findByText(/Não foi possível atualizar o estado/)).toBeVisible();
    expect(screen.queryByText("em execução")).toBeNull();
    expect(screen.getByText("desconhecido")).toBeVisible();
    expect(stop()).toBeDisabled();

    api.failPath = null;
    expect(await screen.findByText("em execução")).toBeVisible();
  });

  it("does not keep an old state on screen when a read never answers", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "running" });
    card(20, 50);
    expect(await screen.findByText("em execução")).toBeVisible();

    api.beforeReply = never;

    expect(await screen.findByText(/Não foi possível atualizar o estado/)).toBeVisible();
    expect(screen.getByText("desconhecido")).toBeVisible();
    expect(stop()).toBeDisabled();

    api.beforeReply = null; // later reads are still made
    expect(await screen.findByText("em execução")).toBeVisible();
  });

  it("goes on reading the state when a command never answers", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    card(20, 50);
    await screen.findByText("parado");
    api.beforeReply = async (request) => {
      if (request.method === "POST") await never();
    };

    await userEvent.click(start());
    api.setAgent(workspaceId, { connected: false, process_state: "unknown" });

    expect(await screen.findByText("Agente desconectado")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Não foi possível falar com o servidor");
  });

  it("reports what happened to the process in plain Portuguese", async () => {
    api.setAgent(workspaceId, {
      paired: true,
      connected: true,
      process_state: "stopped",
      last_event: "exited",
    });
    card();
    expect(await screen.findByText(/terminou sozinho e não foi reiniciado/)).toBeVisible();

    api.setAgent(workspaceId, { last_event: "start_failed" });
    expect(await screen.findByText(/não conseguiu iniciar o processo/)).toBeVisible();
  });

  it("explains a refused command", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    card(60_000);
    await screen.findByText("parado");
    api.setAgent(workspaceId, { connected: false, process_state: "unknown" });

    await userEvent.click(start());

    expect(await screen.findByRole("alert")).toHaveTextContent("agente não está conectado");
    expect(await screen.findByText("Agente desconectado")).toBeVisible(); // reread at once
    expect(document.body).not.toHaveTextContent("server text that must never reach the screen");
  });

  it("removes the agent", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "running" });
    card();
    await screen.findByText("em execução");

    await userEvent.click(screen.getByRole("button", { name: "Remover agente" }));

    expect(await screen.findByText("Nenhum agente pareado neste workspace.")).toBeVisible();
  });

  it("stops asking while the page is hidden", async () => {
    api.setAgent(workspaceId, { paired: true, connected: true, process_state: "stopped" });
    card(10);
    await screen.findByText("parado");

    setHidden(true);
    await new Promise((resolve) => setTimeout(resolve, 40));
    const before = api.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));

    expect(api.calls.length).toBe(before);
  });
});
