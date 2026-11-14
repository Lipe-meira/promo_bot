/**
 * W-01: an operation belongs to the identity and the session that started it.
 *
 * The API authorizes whatever token it receives, so it cannot notice that the data in a request
 * was typed by someone else. These tests drive the whole browser side (SessionGate, forms, client)
 * against a fake API and assert on what is — and, above all, what is *not* — put on the wire.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useLayoutEffect, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { SESSION_CHANGED } from "@/lib/api/client";
import { SessionGate, useSession } from "@/lib/session";
import { clerk, holdTokens, releaseTokens, type PendingToken } from "@/test/clerk-mock";
import { FakeApi } from "@/test/fake-api";
import { navigation, router } from "@/test/navigation-mock";
import { userOfAuthorization } from "@/test/tokens";

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

const onboarding = () => (
  <SessionGate>
    <OnboardingScreen />
  </SessionGate>
);
const workspacePage = (page: ReactNode) => (
  <SessionGate>
    <WorkspaceShell>{page}</WorkspaceShell>
  </SessionGate>
);

/** Lets any request that is going to be issued reach the fake API. */
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 30)));
const writes = () => api.calls.filter((call) => call.method !== "GET");
const nameField = () => screen.findByLabelText("Nome do workspace") as Promise<HTMLInputElement>;

/** User A has the onboarding form open, has typed a name and pressed "create"; the token is pending. */
async function startCreatingAsA(): Promise<{
  view: ReturnType<typeof render>;
  tokens: PendingToken[];
}> {
  const view = render(onboarding());
  await userEvent.type(await nameField(), "Loja da Alice");
  const tokens = holdTokens();
  await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));
  await waitFor(() => expect(tokens).toHaveLength(1));
  return { view, tokens };
}

describe("account switch while a token is pending", () => {
  it("never sends A's typed data under B's token (token resolves for the current session)", async () => {
    const { view, tokens } = await startCreatingAsA();

    clerk.userId = "user_b";
    view.rerender(onboarding());
    await waitFor(() => expect(tokens).toHaveLength(2)); // B's own profile request asks for a token
    tokens[0]!.resolveForCurrentSession(); // A's operation gets B's token: the reported race
    tokens[1]!.resolveForCurrentSession();

    expect((await nameField()).value).toBe(""); // B sees a fresh form, not A's text
    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("drops the old operation even when it settles after the new session is fully loaded", async () => {
    const { view, tokens } = await startCreatingAsA();

    clerk.userId = "user_b";
    view.rerender(onboarding());
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[1]!.resolveForCurrentSession();
    expect((await nameField()).value).toBe("");
    tokens[0]!.resolveForCurrentSession();

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("refuses a token that belongs to someone else even before React has swapped the session", async () => {
    // Clerk may already answer for B while this component tree still renders A.
    const { tokens } = await startCreatingAsA();

    tokens[0]!.resolveFor("user_b");

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("still lets the new account do its own legitimate operation", async () => {
    const { view, tokens } = await startCreatingAsA();
    clerk.userId = "user_b";
    view.rerender(onboarding());
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[0]!.resolveForCurrentSession();
    tokens[1]!.resolveForCurrentSession();
    releaseTokens();

    await userEvent.type(await nameField(), "Loja do Bob");
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(api.workspaces.get("user_b")?.name).toBe("Loja do Bob"));
    expect(api.workspaces.has("user_a")).toBe(false);
    const posts = writes();
    expect(posts).toHaveLength(1);
    expect(userOfAuthorization(posts[0]!.headers.Authorization ?? "")).toBe("user_b");
    expect(posts[0]!.body).toMatchObject({ name: "Loja do Bob" });
  });
});

describe("sign-out and remount", () => {
  it("sends nothing after sign-out, even if the pending token still resolves for the old user", async () => {
    const { view, tokens } = await startCreatingAsA();

    clerk.userId = null;
    view.rerender(onboarding());
    expect(screen.getByRole("status")).toHaveTextContent("Redirecionando para o login");
    tokens[0]!.resolveFor("user_a");

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("sends nothing when the token resolves empty after sign-out", async () => {
    const { view, tokens } = await startCreatingAsA();

    clerk.userId = null;
    view.rerender(onboarding());
    tokens[0]!.resolveNull();

    await settle();
    expect(writes()).toEqual([]);
  });

  it("drops an operation of an unmounted provider even if the same user comes back", async () => {
    const { view, tokens } = await startCreatingAsA();

    view.unmount();
    render(onboarding()); // same user_a, brand-new provider instance
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[0]!.resolveFor("user_a"); // the old operation resolves with a perfectly valid A token
    tokens[1]!.resolveFor("user_a");

    expect((await nameField()).value).toBe(""); // the new instance does not inherit the typed text
    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("does not send a settings change started by A once B is signed in", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    api.seedWorkspace("user_b", "Loja do Bob");
    const view = render(workspacePage(<SettingsScreen />));
    const name = await nameField();
    await userEvent.clear(name);
    await userEvent.type(name, "Nome novo da Alice");
    const tokens = holdTokens();
    await userEvent.click(screen.getByRole("button", { name: "Salvar alterações" }));
    await waitFor(() => expect(tokens).toHaveLength(1));

    clerk.userId = "user_b";
    view.rerender(workspacePage(<SettingsScreen />));
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[0]!.resolveForCurrentSession();
    tokens[1]!.resolveForCurrentSession();
    releaseTokens(); // B's workspace request, issued once its profile is known, needs no hold

    expect((await nameField()).value).toBe("Loja do Bob");
    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.get("user_a")?.name).toBe("Loja da Alice");
    expect(api.workspaces.get("user_b")?.name).toBe("Loja do Bob");
  });
});

describe("the binding does not get in the way of normal use", () => {
  it("works under React StrictMode, where mount effects run twice", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");

    render(
      <StrictMode>{workspacePage(<DashboardScreen />)}</StrictMode>,
    );

    expect(await screen.findByRole("heading", { name: "Loja da Alice", level: 1 })).toBeVisible();
    expect(api.calls.at(-1)!.init.signal?.aborted).toBe(false);
  });

  it("creates a workspace normally for the signed-in user", async () => {
    render(onboarding());
    await userEvent.type(await nameField(), "Loja da Alice");

    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(api.workspaces.get("user_a")?.name).toBe("Loja da Alice"));
    expect(writes()).toHaveLength(1);
  });
});

describe("before the session provider has finished mounting", () => {
  it("drops an operation issued from a child's layout effect (children mount first): fail closed", async () => {
    const outcome: { error?: unknown } = {};
    function EagerChild() {
      const { api } = useSession();
      useLayoutEffect(() => {
        api.getWorkspace("00000000-0000-4000-8000-000000000001").catch((error: unknown) => {
          outcome.error = error;
        });
      }, [api]);
      return null;
    }

    render(
      <SessionGate>
        <EagerChild />
      </SessionGate>,
    );

    await waitFor(() => expect(outcome.error).toMatchObject({ code: SESSION_CHANGED }));
    expect(api.calls.filter((call) => call.path.startsWith("/v1/workspaces"))).toEqual([]);
  });
});

describe("requests already in flight when the session changes", () => {
  it("are aborted, and their late answers never reach the next user's screen", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    api.seedWorkspace("user_b", "Loja do Bob");
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    api.beforeReply = async ({ user }) => {
      if (user === "user_a") await held;
    };
    const view = render(workspacePage(<DashboardScreen />));
    await waitFor(() => expect(api.calls).toHaveLength(1)); // A's profile request is on the wire

    clerk.userId = "user_b";
    view.rerender(workspacePage(<DashboardScreen />));
    expect(await screen.findByRole("heading", { name: "Loja do Bob", level: 1 })).toBeVisible();
    release(); // A's answer finally arrives, after B's

    await settle();
    expect(api.calls[0]!.init.signal?.aborted).toBe(true);
    expect(api.calls[1]!.init.signal?.aborted).toBe(false);
    expect(screen.getByRole("heading", { name: "Loja do Bob", level: 1 })).toBeVisible();
    expect(screen.queryByText("Loja da Alice")).not.toBeInTheDocument();
  });
});
