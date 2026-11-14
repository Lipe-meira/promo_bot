import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { SessionGate } from "@/lib/session";
import { clerk } from "@/test/clerk-mock";
import { errorReply, FakeApi } from "@/test/fake-api";
import { navigation, router } from "@/test/navigation-mock";

import { DashboardScreen } from "./dashboard-screen";
import { OnboardingScreen } from "./onboarding-screen";
import { SettingsScreen } from "./settings-screen";
import { WorkspaceShell } from "./workspace-shell";

let api: FakeApi;

beforeEach(() => {
  api = new FakeApi().install();
  router.replace.mockReset();
  navigation.pathname = "/dashboard";
});

function app(children: ReactNode) {
  return <SessionGate>{children}</SessionGate>;
}

function workspacePage(screen: ReactNode) {
  return app(<WorkspaceShell>{screen}</WorkspaceShell>);
}

describe("session states", () => {
  it("shows a loading state, then the account", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    api.delayMs = 10;

    render(workspacePage(<DashboardScreen />));

    expect(screen.getByRole("status")).toHaveTextContent("Carregando sua conta");
    expect(await screen.findByRole("heading", { name: "Loja da Alice", level: 1 })).toBeVisible();
  });

  it("waits for Clerk before calling the API and never calls it signed out", async () => {
    clerk.isLoaded = false;
    render(workspacePage(<DashboardScreen />));
    expect(screen.getByRole("status")).toHaveTextContent("Verificando sua sessão");

    clerk.isLoaded = true;
    clerk.userId = null;
    render(workspacePage(<DashboardScreen />));

    expect(api.calls).toHaveLength(0);
  });

  it("explains a missing invitation and offers to sign out (403 NOT_ADMITTED)", async () => {
    api.admitted.delete("user_a");

    render(workspacePage(<DashboardScreen />));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading")).toHaveTextContent("Acesso restrito");
    expect(alert).toHaveTextContent("Não há convite ativo");
    expect(within(alert).getByRole("button", { name: "Sair" })).toBeVisible();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(api.calls.map((call) => call.path)).toEqual(["/v1/me"]); // nothing else is requested
  });

  it("asks to sign in again when the API says the token is invalid or expired", async () => {
    api.failNext = errorReply(401, "AUTH_TOKEN_EXPIRED");

    render(workspacePage(<DashboardScreen />));

    expect(await screen.findByRole("alert")).toHaveTextContent("Sua sessão expirou");
    expect(screen.getByRole("button", { name: "Sair" })).toBeVisible();
  });

  it("shows a retryable error for other failures and recovers", async () => {
    api.seedWorkspace("user_a");
    api.failNext = errorReply(503, "NOT_READY");

    render(workspacePage(<DashboardScreen />));
    await userEvent.click(await screen.findByRole("button", { name: "Tentar novamente" }));

    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
  });

  it("never prints server-provided error text", async () => {
    api.failNext = errorReply(500, "INTERNAL_ERROR");

    render(workspacePage(<DashboardScreen />));

    expect(await screen.findByRole("alert")).not.toHaveTextContent("server text");
  });

  it("sends an account without a workspace to the onboarding", async () => {
    render(workspacePage(<DashboardScreen />));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/onboarding"));
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});

describe("user isolation in the browser", () => {
  it("drops everything when the signed-in user changes and shows only the new user's data", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    api.seedWorkspace("user_b", "Loja do Bob");
    const view = render(workspacePage(<DashboardScreen />));
    expect(await screen.findByRole("heading", { name: "Loja da Alice", level: 1 })).toBeVisible();

    clerk.userId = "user_b";
    view.rerender(workspacePage(<DashboardScreen />));

    expect(await screen.findByRole("heading", { name: "Loja do Bob", level: 1 })).toBeVisible();
    expect(screen.queryByText("Loja da Alice")).not.toBeInTheDocument();
    // One profile request per signed-in user, each with its own token, in that order: the second
    // user's session was built from scratch rather than reusing the first user's state.
    expect(
      api.calls.filter((call) => call.path === "/v1/me").map((call) => call.headers.Authorization),
    ).toEqual(["Bearer token-for-user_a", "Bearer token-for-user_b"]);
  });

  it("requests fresh data on every mount instead of reusing a previous answer", async () => {
    api.seedWorkspace("user_a", "Antes");
    const first = render(workspacePage(<DashboardScreen />));
    await screen.findByRole("heading", { name: "Antes", level: 1 });
    first.unmount();
    api.workspaces.set("user_a", { ...api.workspaces.get("user_a")!, name: "Depois" });

    render(workspacePage(<DashboardScreen />));

    expect(await screen.findByRole("heading", { name: "Depois", level: 1 })).toBeVisible();
    expect(api.calls.every((call) => call.init.cache === "no-store")).toBe(true);
  });
});

describe("onboarding", () => {
  it("validates the name before sending anything", async () => {
    render(app(<OnboardingScreen />));
    const input = await screen.findByLabelText("Nome do workspace");

    await userEvent.type(input, " a ");

    expect(screen.getByText("O nome deve ter de 2 a 60 caracteres.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Criar workspace" })).toBeDisabled();
    expect(api.calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("creates the workspace on the server and opens the dashboard", async () => {
    render(app(<OnboardingScreen />));

    await userEvent.type(await screen.findByLabelText("Nome do workspace"), "  Minha   Loja ");
    await userEvent.selectOptions(screen.getByLabelText("Fuso horário"), "America/Manaus");
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
    const post = api.calls.find((call) => call.method === "POST")!;
    expect(post.body).toEqual({ name: "Minha Loja", timezone: "America/Manaus" });
    expect(Object.keys(post.body as object).sort()).toEqual(["name", "timezone"]); // no owner/role
    expect(api.workspaces.get("user_a")?.name).toBe("Minha Loja");
  });

  it("treats an already-existing workspace as success and moves on", async () => {
    api.failNext = null;
    render(app(<OnboardingScreen />));
    await screen.findByLabelText("Nome do workspace");
    api.seedWorkspace("user_a", "Já existia"); // created elsewhere in the meantime

    await userEvent.type(screen.getByLabelText("Nome do workspace"), "Outra");
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(api.workspaces.get("user_a")?.name).toBe("Já existia");
  });

  it("shows server-side validation failures next to the form, without echoing values", async () => {
    render(app(<OnboardingScreen />));
    await userEvent.type(await screen.findByLabelText("Nome do workspace"), "Loja");
    api.failPath = {
      match: /\/v1\/workspaces$/,
      reply: errorReply(422, "VALIDATION_ERROR", { fields: [{ field: "timezone", type: "x" }] }),
    };

    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Alguns campos são inválidos");
    expect(alert).toHaveTextContent("Fuso horário");
    expect(alert).not.toHaveTextContent("server text");
    expect(screen.getByLabelText("Nome do workspace")).toHaveValue("Loja"); // input kept
  });

  it("goes straight to the dashboard when a workspace already exists", async () => {
    api.seedWorkspace("user_a");

    render(app(<OnboardingScreen />));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("disables the button while saving to avoid duplicate submissions", async () => {
    api.delayMs = 30;
    render(app(<OnboardingScreen />));
    await userEvent.type(await screen.findByLabelText("Nome do workspace"), "Loja");

    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    expect(screen.getByRole("button", { name: "Salvando…" })).toBeDisabled();
    await waitFor(() => expect(router.replace).toHaveBeenCalled());
    expect(api.calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });
});

describe("dashboard", () => {
  beforeEach(() => api.seedWorkspace("user_a", "Loja da Alice"));

  it("shows the real status and never claims anything is online", async () => {
    render(workspacePage(<DashboardScreen />));

    const status = await screen.findByRole("region", { name: "Operação" });
    expect(await within(status).findByText("Nada em operação")).toBeVisible();
    expect(within(status).getByText(/Não provisionado/)).toBeVisible();
    expect(within(status).getByText(/Não conectado/)).toBeVisible();
    expect(within(status).getByText(/Não configurado/)).toBeVisible();
    expect(within(status).getByText("Nenhum worker foi provisionado.")).toBeVisible();
    expect(screen.queryByText(/online/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Último sinal/)).not.toBeInTheDocument(); // no heartbeat, no claim
  });

  it("lists capabilities as unavailable with their reasons, and offers no operational action", async () => {
    render(workspacePage(<DashboardScreen />));

    const section = await screen.findByRole("region", { name: "Recursos" });
    expect(await within(section).findByText("Conexão com o Telegram")).toBeVisible();
    expect(within(section).getByText("O login por QR ainda não foi implementado.")).toBeVisible();
    expect(within(section).getAllByText("Indisponível")).toHaveLength(2);
    expect(within(section).getAllByText("Disponível")).toHaveLength(1);
    expect(screen.queryByRole("button")).not.toBeInTheDocument(); // only the user menu mock is a div
  });

  it("isolates a failing section and retries only that section", async () => {
    api.failPath = { match: /\/status$/, reply: errorReply(503, "NOT_READY") };

    render(workspacePage(<DashboardScreen />));

    const status = await screen.findByRole("region", { name: "Operação" });
    expect(await within(status).findByRole("alert")).toHaveTextContent("ainda não está pronto");
    expect(await screen.findByText("Conexão com o Telegram")).toBeVisible();

    api.failPath = null;
    await userEvent.click(within(status).getByRole("button", { name: "Tentar novamente" }));
    expect(await within(status).findByText("Nada em operação")).toBeVisible();
  });

  it("shows only links to screens that exist", async () => {
    render(workspacePage(<DashboardScreen />));

    const nav = await screen.findByRole("navigation", { name: "Principal" });
    expect(within(nav).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/dashboard",
      "/settings",
    ]);
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
  });
});

describe("settings", () => {
  beforeEach(() => api.seedWorkspace("user_a", "Loja da Alice"));

  it("saves, and a fresh load confirms the change was persisted on the server", async () => {
    const first = render(workspacePage(<SettingsScreen />));
    const name = await screen.findByLabelText("Nome do workspace");
    await userEvent.clear(name);
    await userEvent.type(name, "Nome novo");
    await userEvent.selectOptions(screen.getByLabelText("Fuso horário"), "Europe/Lisbon");

    await userEvent.click(screen.getByRole("button", { name: "Salvar alterações" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Alterações salvas");
    expect(await screen.findByText(/Versão 2/)).toBeVisible();
    const patch = api.calls.find((call) => call.method === "PATCH")!;
    expect(patch.body).toEqual({ version: 1, name: "Nome novo", timezone: "Europe/Lisbon" });

    first.unmount(); // "reload the page"
    render(workspacePage(<SettingsScreen />));
    expect(await screen.findByLabelText("Nome do workspace")).toHaveValue("Nome novo");
    expect(screen.getByLabelText("Fuso horário")).toHaveValue("Europe/Lisbon");
  });

  it("sends only the fields that changed", async () => {
    render(workspacePage(<SettingsScreen />));
    const name = await screen.findByLabelText("Nome do workspace");
    await userEvent.clear(name);
    await userEvent.type(name, "Só o nome");

    await userEvent.click(screen.getByRole("button", { name: "Salvar alterações" }));

    await screen.findByText(/Versão 2/);
    expect(api.calls.find((call) => call.method === "PATCH")!.body).toEqual({
      version: 1,
      name: "Só o nome",
    });
  });

  it("keeps save disabled until something changes", async () => {
    render(workspacePage(<SettingsScreen />));

    expect(await screen.findByRole("button", { name: "Salvar alterações" })).toBeDisabled();
  });

  it("reports a version conflict and reloads the server's current version on request", async () => {
    render(workspacePage(<SettingsScreen />));
    const name = await screen.findByLabelText("Nome do workspace");
    // Someone else saves first.
    api.workspaces.set("user_a", { ...api.workspaces.get("user_a")!, name: "Do outro lado", version: 2 });
    await userEvent.clear(name);
    await userEvent.type(name, "Minha edição");

    await userEvent.click(screen.getByRole("button", { name: "Salvar alterações" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("foi alterado em outro lugar");
    expect(api.workspaces.get("user_a")?.name).toBe("Do outro lado"); // nothing was overwritten
    await userEvent.click(screen.getByRole("button", { name: "Recarregar versão atual" }));
    expect(await screen.findByLabelText("Nome do workspace")).toHaveValue("Do outro lado");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows loading and error states for the settings request", async () => {
    api.failPath = { match: /\/v1\/workspaces\/[^/]+$/, reply: errorReply(404, "WORKSPACE_NOT_FOUND") };

    render(workspacePage(<SettingsScreen />));

    expect(await screen.findByRole("alert")).toHaveTextContent("Workspace não encontrado");
  });
});
