/**
 * W-01 (final): an operation belongs to the Clerk *session* that started it, not only to a user.
 *
 * Two windows the user-only check left open, both reproduced by the independent audit:
 *
 *   R1  Clerk replaces session A1 with A2 of the *same* user while a token is pending: `sub` is
 *       equal, `sid` differs, and the old operation used to go out under A2.
 *   R2  Clerk ends the session but React has not unmounted the SessionProvider yet: the pending
 *       token still resolves with the original `sub` and `sid`, and the POST used to go out after
 *       the sign-out.
 *
 * The Clerk mock keeps the *effective* state of Clerk apart from what React has rendered: the
 * helpers below change the former synchronously and never re-render, so the tree stays mounted with
 * the identity it last rendered (that is the delay under test) until a test calls `rerender`.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useEffect, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { SESSION_CHANGED } from "@/lib/api/client";
import { SessionGate, useSession } from "@/lib/session";
import {
  clerk,
  emitLoading,
  holdTokens,
  listenerCount,
  listenerLog,
  releaseTokens,
  replaceSession,
  setSessionStatus,
  signOutNow,
} from "@/test/clerk-mock";
import { FakeApi } from "@/test/fake-api";
import { navigation, router } from "@/test/navigation-mock";
import { defaultSessionOf, sessionOfAuthorization, userOfAuthorization } from "@/test/tokens";

import { DashboardScreen } from "./dashboard-screen";
import { OnboardingScreen } from "./onboarding-screen";
import { SettingsScreen } from "./settings-screen";
import { WorkspaceShell } from "./workspace-shell";

const A1 = defaultSessionOf("user_a");
const A2 = "sess_user_a_second";

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

/** User A (session A1) has typed a name and pressed "create"; the token is pending. */
async function startCreatingAsA() {
  const view = render(onboarding());
  await userEvent.type(await nameField(), "Loja da Alice");
  const tokens = holdTokens();
  await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));
  await waitFor(() => expect(tokens).toHaveLength(1));
  return { view, tokens };
}

/** User A (session A1) has edited the workspace name and pressed "save"; the token is pending. */
async function startSavingAsA() {
  api.seedWorkspace("user_a", "Loja da Alice");
  const view = render(workspacePage(<SettingsScreen />));
  const name = await nameField();
  await userEvent.clear(name);
  await userEvent.type(name, "Nome novo da Alice");
  const tokens = holdTokens();
  await userEvent.click(screen.getByRole("button", { name: "Salvar alterações" }));
  await waitFor(() => expect(tokens).toHaveLength(1));
  return { view, tokens };
}

describe("R1 — another session of the same user replaces the one that started the operation", () => {
  it("never sends a POST begun under A1 with A2's token (same sub, different sid)", async () => {
    const { tokens } = await startCreatingAsA();

    replaceSession("user_a", A2); // Clerk: A1 -> A2; React still renders A1
    tokens[0]!.resolveFor("user_a", A2); // the old operation is handed A2's token

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("holds even if Clerk emits no notification at all (the pre-send check alone is enough)", async () => {
    const { tokens } = await startCreatingAsA();
    clerk.notify = false;

    replaceSession("user_a", A2);
    tokens[0]!.resolveFor("user_a", A2);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("refuses A1's own token too once A2 is the active session", async () => {
    const { tokens } = await startCreatingAsA();
    clerk.notify = false; // nothing aborts the operation: only the live check can stop it

    replaceSession("user_a", A2);
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("drops the PATCH of a settings change begun under A1 when A2 is active", async () => {
    const { tokens } = await startSavingAsA();

    replaceSession("user_a", A2);
    tokens[0]!.resolveFor("user_a", A2);

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.get("user_a")?.name).toBe("Loja da Alice");
  });

  it("still drops it once React catches up, and the new session starts from a clean form", async () => {
    const { view, tokens } = await startCreatingAsA();

    replaceSession("user_a", A2);
    view.rerender(onboarding());
    await waitFor(() => expect(tokens).toHaveLength(2)); // A2's own profile request
    tokens[0]!.resolveFor("user_a", A2);
    tokens[1]!.resolveFor("user_a", A2);

    expect((await nameField()).value).toBe(""); // A2 does not inherit A1's text
    await settle();
    expect(writes()).toEqual([]);
  });

  it("lets a new, legitimate operation of A2 through exactly once, under A2's token", async () => {
    const { view, tokens } = await startCreatingAsA();
    replaceSession("user_a", A2);
    view.rerender(onboarding());
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[0]!.resolveFor("user_a", A2);
    tokens[1]!.resolveFor("user_a", A2);
    releaseTokens();

    await userEvent.type(await nameField(), "Loja nova");
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(api.workspaces.get("user_a")?.name).toBe("Loja nova"));
    const posts = writes();
    expect(posts).toHaveLength(1);
    expect(sessionOfAuthorization(posts[0]!.headers.Authorization ?? "")).toBe(A2);
    expect(posts[0]!.body).toMatchObject({ name: "Loja nova" });
  });
});

describe("R2 — Clerk ended the session but React has not unmounted the provider", () => {
  it("never sends the POST when the old token resolves with the original sub and sid", async () => {
    const { tokens } = await startCreatingAsA();

    signOutNow(); // Clerk: session ended; NO re-render, the provider is still mounted
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("holds even if Clerk emits no notification at all (the pre-send check alone is enough)", async () => {
    const { tokens } = await startCreatingAsA();
    clerk.notify = false;

    signOutNow();
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("drops the PATCH of a settings change too", async () => {
    const { tokens } = await startSavingAsA();

    signOutNow();
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.get("user_a")?.name).toBe("Loja da Alice");
  });

  it("still drops it once React unmounts (sign-out with unmount)", async () => {
    const { view, tokens } = await startCreatingAsA();

    signOutNow();
    view.rerender(onboarding());
    expect(screen.getByRole("status")).toHaveTextContent("Redirecionando para o login");
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("drops it when the same user signs in again (new session) before React ever re-rendered", async () => {
    const { tokens } = await startCreatingAsA();

    signOutNow();
    replaceSession("user_a", A2); // log out and straight back in: the tree never saw the gap
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("drops it when the same user signs in again and the token is the new session's", async () => {
    const { tokens } = await startCreatingAsA();

    signOutNow();
    replaceSession("user_a", A2);
    tokens[0]!.resolveFor("user_a", A2);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("lets the same user act again after signing back in and React catching up", async () => {
    const { view, tokens } = await startCreatingAsA();
    signOutNow();
    view.rerender(onboarding());
    replaceSession("user_a", A2);
    view.rerender(onboarding());
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[0]!.resolveFor("user_a", A1);
    tokens[1]!.resolveFor("user_a", A2);
    releaseTokens();

    await userEvent.type(await nameField(), "De volta");
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(api.workspaces.get("user_a")?.name).toBe("De volta"));
    expect(writes()).toHaveLength(1);
  });
});

describe("another user takes over before React re-renders", () => {
  it("never sends A's POST even when the token resolves for A (sub still matches the render)", async () => {
    const { tokens } = await startCreatingAsA();

    replaceSession("user_b"); // Clerk: B is now active; React still renders A
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("never sends it under B's token either", async () => {
    const { tokens } = await startCreatingAsA();

    replaceSession("user_b");
    tokens[0]!.resolveForCurrentSession();

    await settle();
    expect(writes()).toEqual([]);
  });
});

describe("the session stops being active without being replaced", () => {
  it.each(["ended", "revoked", "expired", "replaced", "pending"] as const)(
    "sends nothing when the session is %s (token still resolves with A1's claims)",
    async (status) => {
      const { tokens } = await startCreatingAsA();
      clerk.notify = false;

      setSessionStatus(status);
      tokens[0]!.resolveFor("user_a", A1);

      await settle();
      expect(writes()).toEqual([]);
    },
  );
});

describe("the live Clerk state must still be the original one, whatever the token says", () => {
  it("refuses when the active user is not the original even if the session id coincided", async () => {
    // Not something Clerk produces (session ids are unique); it proves the user is checked on its own.
    const { tokens } = await startCreatingAsA();
    clerk.notify = false;

    replaceSession("user_b", A1);
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("refuses while Clerk reports that it is not loaded", async () => {
    const { tokens } = await startCreatingAsA();
    clerk.notify = false;

    clerk.isLoaded = false; // live state only: the mounted tree is not re-rendered
    tokens[0]!.resolveFor("user_a", A1);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("refuses an operation when Clerk had already moved on before the provider's effect ran", async () => {
    let outcome: unknown;
    function Probe() {
      const { api } = useSession();
      useEffect(() => {
        api.getWorkspace("00000000-0000-4000-8000-000000000001").catch((error: unknown) => {
          outcome = error;
        });
      }, [api]);
      return null;
    }
    function MovesClerkWhileRendering() {
      replaceSession("user_a", A2); // during the first render, before the provider's layout effect
      return null;
    }

    render(
      <SessionGate>
        <MovesClerkWhileRendering />
        <Probe />
      </SessionGate>,
    );

    await waitFor(() => expect(outcome).toMatchObject({ code: SESSION_CHANGED }));
    expect(api.calls.filter((call) => call.path.startsWith("/v1/workspaces"))).toEqual([]);
  });
});

describe("tokens that do not prove the session", () => {
  it.each([
    ["no sid claim", { sid: undefined }],
    ["empty sid", { sid: "" }],
    ["non-string sid", { sid: 7 }],
    ["object sid", { sid: { id: A1 } }],
  ])("refuses a token with %s and sends nothing", async (_name, claims) => {
    const { tokens } = await startCreatingAsA();

    tokens[0]!.resolveWith("user_a", claims);

    await settle();
    expect(writes()).toEqual([]);
    expect(api.workspaces.size).toBe(0);
  });

  it("refuses an unreadable token and sends nothing", async () => {
    const { tokens } = await startCreatingAsA();
    tokens[0]!.resolveRaw("not-a-jwt");

    await settle();
    expect(writes()).toEqual([]);
  });
});

describe("answers that arrive after the session was replaced", () => {
  it("aborts the in-flight request the moment Clerk replaces the session, before React re-renders", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    api.beforeReply = async () => held;
    render(workspacePage(<DashboardScreen />));
    await waitFor(() => expect(api.calls).toHaveLength(1)); // A1's request is on the wire

    replaceSession("user_a", A2); // no re-render

    expect(api.calls[0]!.init.signal?.aborted).toBe(true);
    release();
    await settle();
    expect(screen.queryByText("Loja da Alice")).not.toBeInTheDocument();
  });

  it("never delivers an answer to A1's request after A2 took over, even with no notification", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    api.beforeReply = async () => held;
    render(workspacePage(<DashboardScreen />));
    await waitFor(() => expect(api.calls).toHaveLength(1));
    clerk.notify = false;

    replaceSession("user_a", A2);
    release();

    await settle();
    expect(screen.queryByText("Loja da Alice")).not.toBeInTheDocument();
  });

  it("never delivers an answer after the session ended and React has not unmounted yet", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    api.beforeReply = async () => held;
    render(workspacePage(<DashboardScreen />));
    await waitFor(() => expect(api.calls).toHaveLength(1));

    signOutNow();
    release();

    await settle();
    expect(screen.queryByText("Loja da Alice")).not.toBeInTheDocument();
  });
});

describe("remount and StrictMode", () => {
  it("drops an operation of an unmounted provider even if the same user and session come back", async () => {
    const { view, tokens } = await startCreatingAsA();

    view.unmount();
    render(onboarding()); // same user_a, same session A1, brand-new provider instance
    await waitFor(() => expect(tokens).toHaveLength(2));
    tokens[0]!.resolveFor("user_a", A1);
    tokens[1]!.resolveFor("user_a", A1);

    expect((await nameField()).value).toBe("");
    await settle();
    expect(writes()).toEqual([]);
  });

  it("creates a workspace exactly once under StrictMode (effects run twice)", async () => {
    render(<StrictMode>{onboarding()}</StrictMode>);
    await userEvent.type(await nameField(), "Loja da Alice");

    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(api.workspaces.get("user_a")?.name).toBe("Loja da Alice"));
    expect(writes()).toHaveLength(1);
    expect(sessionOfAuthorization(writes()[0]!.headers.Authorization ?? "")).toBe(A1);
  });

  it("still refuses the stale operation under StrictMode (R1 and R2)", async () => {
    render(<StrictMode>{onboarding()}</StrictMode>);
    await userEvent.type(await nameField(), "Loja da Alice");
    const tokens = holdTokens();
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));
    await waitFor(() => expect(tokens).toHaveLength(1));

    replaceSession("user_a", A2);
    tokens[0]!.resolveFor("user_a", A2);

    await settle();
    expect(writes()).toEqual([]);
  });

  it("does not treat a transient 'loading' emission from Clerk as the end of the session", async () => {
    render(onboarding());
    await userEvent.type(await nameField(), "Loja da Alice");

    emitLoading();
    await userEvent.click(screen.getByRole("button", { name: "Criar workspace" }));

    await waitFor(() => expect(api.workspaces.get("user_a")?.name).toBe("Loja da Alice"));
    expect(writes()).toHaveLength(1);
  });
});

describe("listeners do not leak", () => {
  it("keeps exactly one listener while a provider is mounted and none after it unmounts", async () => {
    const view = render(onboarding());
    await nameField();
    expect(listenerCount()).toBe(1);

    view.unmount();

    expect(listenerCount()).toBe(0);
  });

  it("keeps exactly one listener under StrictMode, where mount effects run twice", async () => {
    const view = render(<StrictMode>{onboarding()}</StrictMode>);
    await nameField();
    expect(listenerCount()).toBe(1);

    view.unmount();

    expect(listenerCount()).toBe(0);
    expect(listenerLog.added).toBe(listenerLog.removed);
  });

  it("swaps the listener when the session changes (no listener of the old session survives)", async () => {
    const view = render(onboarding());
    await nameField();

    replaceSession("user_a", A2);
    view.rerender(onboarding());
    await nameField();
    expect(listenerCount()).toBe(1);

    signOutNow();
    view.rerender(onboarding());
    expect(listenerCount()).toBe(0);
  });

  it("does not accumulate listeners across repeated mounts and unmounts", async () => {
    for (let round = 0; round < 3; round += 1) {
      const view = render(onboarding());
      await nameField();
      expect(listenerCount()).toBe(1);
      view.unmount();
      expect(listenerCount()).toBe(0);
    }
    expect(listenerLog.added).toBe(3);
    expect(listenerLog.removed).toBe(3);
  });
});

describe("a token that was fetched for an earlier user is never what a later user's request carries", () => {
  it("each request carries its own user and session", async () => {
    api.seedWorkspace("user_a", "Loja da Alice");
    api.seedWorkspace("user_b", "Loja do Bob");
    const view = render(workspacePage(<DashboardScreen />));
    expect(await screen.findByRole("heading", { name: "Loja da Alice", level: 1 })).toBeVisible();

    replaceSession("user_b");
    view.rerender(workspacePage(<DashboardScreen />));
    expect(await screen.findByRole("heading", { name: "Loja do Bob", level: 1 })).toBeVisible();

    const seen = api.calls
      .filter((call) => call.path === "/v1/me")
      .map((call) => [
        userOfAuthorization(call.headers.Authorization ?? ""),
        sessionOfAuthorization(call.headers.Authorization ?? ""),
      ]);
    expect(seen).toEqual([
      ["user_a", A1],
      ["user_b", defaultSessionOf("user_b")],
    ]);
  });
});
