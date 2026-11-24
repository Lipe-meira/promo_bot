import { describe, expect, it, vi } from "vitest";

import { defaultSessionOf, tokenFor } from "@/test/tokens";

import {
  ApiClientError,
  createApiClient,
  NETWORK_ERROR,
  SESSION_CHANGED,
  UNEXPECTED_RESPONSE,
  type ApiSession,
  type TokenGetter,
} from "./client";
import { admissionReason, describeError, invalidFields } from "./messages";

const ID = "11111111-1111-4111-8111-111111111111";
const TOKEN = tokenFor("user_a");

function reply(status: number, body: unknown) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
}

/** What Clerk reports right now about the session the client is bound to; tests flip it. */
interface Live {
  active: boolean;
}

function sessionFor(
  getToken: TokenGetter,
  controller = new AbortController(),
  userId = "user_a",
  live: Live = { active: true },
  sessionId = defaultSessionOf(userId),
): ApiSession {
  return { userId, sessionId, getToken, signal: () => controller.signal, isActive: () => live.active };
}

function clientWith(fetchImpl: typeof fetch, token: string | null = TOKEN) {
  return createApiClient("http://api.test/", sessionFor(async () => token), fetchImpl);
}

describe("request shape", () => {
  it("sends only the bearer token: no cache, no cookies, no identity or role", async () => {
    const fetchMock = vi.fn(async () => reply(200, { user: {}, workspaces: [] }));

    await clientWith(fetchMock as unknown as typeof fetch).me();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/v1/me");
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("omit");
    expect(init.headers).toEqual({ Accept: "application/json", Authorization: `Bearer ${TOKEN}` });
  });

  it("asks Clerk for a fresh token on every call", async () => {
    const first = tokenFor("user_a", { jti: "first" });
    const second = tokenFor("user_a", { jti: "second" });
    const getToken = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    const seen: string[] = [];
    const fetchMock = async (_url: string, init: RequestInit) => {
      seen.push((init.headers as Record<string, string>).Authorization!);
      return reply(200, { user: {}, workspaces: [] });
    };
    const api = createApiClient(
      "http://api.test",
      sessionFor(getToken),
      fetchMock as unknown as typeof fetch,
    );

    await api.me();
    await api.me();

    expect(seen).toEqual([`Bearer ${first}`, `Bearer ${second}`]);
  });

  it("keeps no state between calls (no response cache)", async () => {
    let calls = 0;
    const fetchMock = async () => reply(200, { n: ++calls });
    const api = clientWith(fetchMock as unknown as typeof fetch);

    const first = await api.getWorkspace(ID);
    const second = await api.getWorkspace(ID);

    expect([first, second]).toEqual([{ n: 1 }, { n: 2 }]);
  });

  it("sends JSON bodies only when there is one and encodes path parameters", async () => {
    const fetchMock = vi.fn(async () => reply(200, {}));
    const api = clientWith(fetchMock as unknown as typeof fetch);

    await api.createWorkspace({ name: "Loja", timezone: "UTC" });
    await api.updateWorkspace("a/b?c", { version: 2, name: "Nova" });
    await api.getStatus(ID);

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0]![1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ name: "Loja", timezone: "UTC" }),
    });
    expect((calls[0]![1].headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(calls[1]![0]).toBe("http://api.test/v1/workspaces/a%2Fb%3Fc");
    expect(calls[1]![1].method).toBe("PATCH");
    expect(calls[2]![1].body).toBeUndefined();
    expect((calls[2]![1].headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("does not call the network without a token", async () => {
    const fetchMock = vi.fn();

    await expect(clientWith(fetchMock as unknown as typeof fetch, null).me()).rejects.toMatchObject({
      code: "AUTH_INVALID_TOKEN",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("an operation is bound to the session that started it (W-01)", () => {
  const ok = () => reply(200, { user: {}, workspaces: [] });

  function setup(getToken: TokenGetter, userId = "user_a") {
    const controller = new AbortController();
    const live: Live = { active: true };
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ok());
    const api = createApiClient(
      "http://api.test",
      sessionFor(getToken, controller, userId, live),
      fetchMock as unknown as typeof fetch,
    );
    return { controller, live, fetchMock, api };
  }

  it("does not even ask for a token once its session is over", async () => {
    const getToken = vi.fn(async () => TOKEN);
    const { controller, fetchMock, api } = setup(getToken);
    controller.abort();

    await expect(api.createWorkspace({ name: "Loja", timezone: "UTC" })).rejects.toMatchObject({
      code: SESSION_CHANGED,
    });
    expect(getToken).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not send when the session ends while the token is pending", async () => {
    let settle: (token: string) => void = () => {};
    const { controller, fetchMock, api } = setup(
      () => new Promise<string>((resolve) => (settle = resolve)),
    );

    const operation = api.createWorkspace({ name: "Loja", timezone: "UTC" });
    controller.abort();
    settle(TOKEN); // a perfectly valid token for the same user, but for a session that is gone

    await expect(operation).rejects.toMatchObject({ code: SESSION_CHANGED });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not send when the pending token belongs to another user, session not yet torn down", async () => {
    const { fetchMock, api } = setup(async () => tokenFor("user_b"));

    await expect(api.updateWorkspace(ID, { version: 1, name: "Nova" })).rejects.toMatchObject({
      code: SESSION_CHANGED,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["not a JWT", "opaque-token"],
    ["no subject", `x.${btoa(JSON.stringify({ iss: "i" }))}.y`],
    ["undecodable payload", "x.!!!.y"],
    ["non-string subject", `x.${btoa(JSON.stringify({ sub: 7 }))}.y`],
    ["no session claim", tokenFor("user_a", { sid: undefined })],
    ["empty session claim", tokenFor("user_a", { sid: "" })],
    ["non-string session claim", tokenFor("user_a", { sid: 7 })],
    ["null session claim", tokenFor("user_a", { sid: null })],
  ])("fails closed on a token whose owner cannot be read (%s)", async (_name, token) => {
    const { fetchMock, api } = setup(async () => token);

    await expect(api.me()).rejects.toMatchObject({ code: "AUTH_INVALID_TOKEN" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not send a token of another session of the same user (same sub, different sid)", async () => {
    const { fetchMock, api } = setup(async () => tokenFor("user_a", { sid: "sess_user_a_second" }));

    await expect(api.createWorkspace({ name: "Loja", timezone: "UTC" })).rejects.toMatchObject({
      code: SESSION_CHANGED,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not send a token of another user even if its sid is the session's (sub is checked on its own)", async () => {
    const { fetchMock, api } = setup(async () =>
      tokenFor("user_b", { sid: defaultSessionOf("user_a") }),
    );

    await expect(api.me()).rejects.toMatchObject({ code: SESSION_CHANGED });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends a token whose sub and sid are exactly the session's", async () => {
    const { fetchMock, api } = setup(async () => tokenFor("user_a", { sid: defaultSessionOf("user_a") }));

    await api.me();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not even ask for a token when Clerk already reports the session as not active", async () => {
    const getToken = vi.fn(async () => TOKEN);
    const { live, fetchMock, api } = setup(getToken);
    live.active = false;

    await expect(api.me()).rejects.toMatchObject({ code: SESSION_CHANGED });
    expect(getToken).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not send when the session stops being active while the token is pending, though nothing aborted it", async () => {
    let settle: (token: string) => void = () => {};
    const { controller, live, fetchMock, api } = setup(
      () => new Promise<string>((resolve) => (settle = resolve)),
    );

    const operation = api.createWorkspace({ name: "Loja", timezone: "UTC" });
    live.active = false; // Clerk ended/replaced the session; React (and so the signal) not told yet
    settle(TOKEN); // the original session's own, perfectly valid token

    await expect(operation).rejects.toMatchObject({ code: SESSION_CHANGED });
    expect(controller.signal.aborted).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a missing token after the session ended as a changed session, not an expired one", async () => {
    let settle: (token: string | null) => void = () => {};
    const { live, fetchMock, api } = setup(
      () => new Promise<string | null>((resolve) => (settle = resolve)),
    );

    const operation = api.me();
    live.active = false;
    settle(null);

    await expect(operation).rejects.toMatchObject({ code: SESSION_CHANGED });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never delivers a response that arrives after the session stopped being active, though nothing aborted it", async () => {
    const live: Live = { active: true };
    const fetchMock = async () => {
      live.active = false;
      return ok();
    };
    const api = createApiClient(
      "http://api.test",
      sessionFor(async () => TOKEN, new AbortController(), "user_a", live),
      fetchMock as unknown as typeof fetch,
    );

    await expect(api.me()).rejects.toMatchObject({ code: SESSION_CHANGED });
  });

  it("reports a network failure after the session stopped being active as a changed session", async () => {
    const live: Live = { active: true };
    const fetchMock = async () => {
      live.active = false;
      throw new TypeError("network down");
    };
    const api = createApiClient(
      "http://api.test",
      sessionFor(async () => TOKEN, new AbortController(), "user_a", live),
      fetchMock as unknown as typeof fetch,
    );

    await expect(api.me()).rejects.toMatchObject({ code: SESSION_CHANGED });
  });

  it("hands the session's signal to fetch so an in-flight request is aborted with it", async () => {
    const { controller, fetchMock, api } = setup(async () => TOKEN);

    await api.me();

    const init = fetchMock.mock.calls[0]![1];
    expect(init.signal).toBe(controller.signal);
  });

  it("reports an abort during the request as a changed session, not as a network failure", async () => {
    const controller = new AbortController();
    let onRequest: () => void = () => {};
    const requested = new Promise<void>((resolve) => (onRequest = resolve));
    const fetchMock = (_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        onRequest();
      });
    const api = createApiClient(
      "http://api.test",
      sessionFor(async () => TOKEN, controller),
      fetchMock as unknown as typeof fetch,
    );

    const operation = api.me();
    await requested; // the request is on the wire
    controller.abort();

    await expect(operation).rejects.toMatchObject({ code: SESSION_CHANGED });
  });

  it("never delivers a response that arrives after the session ended", async () => {
    const controller = new AbortController();
    const fetchMock = async () => {
      controller.abort(); // the session ends while the answer is on its way back
      return ok();
    };
    const api = createApiClient(
      "http://api.test",
      sessionFor(async () => TOKEN, controller),
      fetchMock as unknown as typeof fetch,
    );

    await expect(api.me()).rejects.toMatchObject({ code: SESSION_CHANGED });
  });

  it("tells the person to repeat the action, in plain words", () => {
    expect(describeError(new ApiClientError(0, SESSION_CHANGED, "x"))).toBe(
      "Sua sessão mudou. Repita a ação.",
    );
  });
});

describe("errors", () => {
  async function failure(response: Response | Error) {
    const fetchMock = async () => {
      if (response instanceof Error) throw response;
      return response;
    };
    return clientWith(fetchMock as unknown as typeof fetch)
      .me()
      .then(
        () => {
          throw new Error("expected a rejection");
        },
        (error: unknown) => error as ApiClientError,
      );
  }

  it("parses the API error envelope", async () => {
    const error = await failure(
      reply(409, { error: { code: "WORKSPACE_VERSION_CONFLICT", message: "x", details: { current_version: 4 } } }),
    );

    expect(error).toBeInstanceOf(ApiClientError);
    expect([error.status, error.code, error.details]).toEqual([
      409,
      "WORKSPACE_VERSION_CONFLICT",
      { current_version: 4 },
    ]);
  });

  it.each([
    ["html from a proxy", reply(502, "<html>bad gateway</html>")],
    ["empty body", reply(500, "")],
    ["wrong json shape", reply(400, { detail: "nope" })],
    ["error without code", reply(400, { error: { message: "x" } })],
  ])("treats %s as an unexpected response", async (_name, response) => {
    const error = await failure(response);

    expect(error.code).toBe(UNEXPECTED_RESPONSE);
    expect(describeError(error)).toBe("Algo deu errado. Tente novamente.");
  });

  it("treats a successful response without a JSON body as unexpected", async () => {
    expect((await failure(reply(200, ""))).code).toBe(UNEXPECTED_RESPONSE);
  });

  it("maps a network failure to its own code and a friendly message", async () => {
    const error = await failure(new TypeError("Failed to fetch"));

    expect([error.status, error.code]).toEqual([0, NETWORK_ERROR]);
    expect(describeError(error)).toContain("conexão");
  });

  it("never shows server-provided text to the person", () => {
    const error = new ApiClientError(500, "INTERNAL_ERROR", "SELECT * FROM users failed at 10.0.0.5");

    expect(describeError(error)).not.toContain("SELECT");
    expect(describeError(new Error("boom"))).toBe("Algo deu errado. Tente novamente.");
  });
});

describe("message helpers", () => {
  it("explains admission reasons and ignores unknown ones", () => {
    const known = new ApiClientError(403, "NOT_ADMITTED", "x", { reason: "NO_INVITATION" });
    const unknown = new ApiClientError(403, "NOT_ADMITTED", "x", { reason: "SOMETHING_NEW" });

    expect(admissionReason(known)).toContain("convite");
    expect(admissionReason(unknown)).toBeNull();
    expect(admissionReason(new ApiClientError(403, "NOT_ADMITTED", "x"))).toBeNull();
  });

  it("extracts only field names from validation errors", () => {
    const error = new ApiClientError(422, "VALIDATION_ERROR", "x", {
      fields: [{ field: "name", type: "value_error" }, { bad: true }, { field: "timezone" }],
    });

    expect(invalidFields(error)).toEqual(["name", "timezone"]);
    expect(invalidFields(new ApiClientError(409, "OTHER", "x"))).toEqual([]);
    expect(invalidFields(new Error("x"))).toEqual([]);
  });
});

describe("telegram channel operations", () => {
  const CHAT = "-1001000000001";
  const config = { workspace_id: ID, sources: [], destination: null, max_sources: 50, access_status: "not_verified" };

  async function call(run: (api: ReturnType<typeof clientWith>) => Promise<unknown>) {
    const fetchMock = vi.fn(async () => reply(200, config));
    await run(clientWith(fetchMock as unknown as typeof fetch));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    return { url, init };
  }

  it("uses the documented routes, methods and bodies, with only the chat id as input", async () => {
    const base = `http://api.test/v1/workspaces/${ID}/telegram-channels`;

    const read = await call((api) => api.getTelegramChannels(ID));
    const add = await call((api) => api.addSourceChannel(ID, CHAT));
    const remove = await call((api) => api.removeSourceChannel(ID, CHAT));
    const save = await call((api) => api.setDestinationChannel(ID, CHAT));

    expect([read.url, read.init.method, read.init.body]).toEqual([base, "GET", undefined]);
    expect([add.url, add.init.method, add.init.body]).toEqual([
      `${base}/sources`,
      "POST",
      JSON.stringify({ chat_id: CHAT }),
    ]);
    expect([remove.url, remove.init.method, remove.init.body]).toEqual([
      `${base}/sources/${CHAT}`,
      "DELETE",
      undefined,
    ]);
    expect([save.url, save.init.method, save.init.body]).toEqual([
      `${base}/destination`,
      "PUT",
      JSON.stringify({ chat_id: CHAT }),
    ]);
  });

  it("surfaces the stable error code of a refusal", async () => {
    const fetchMock = vi.fn(async () =>
      reply(409, { error: { code: "SOURCE_CHANNEL_DUPLICATE", message: "raw" } }),
    );

    await expect(
      clientWith(fetchMock as unknown as typeof fetch).addSourceChannel(ID, CHAT),
    ).rejects.toMatchObject({ status: 409, code: "SOURCE_CHANNEL_DUPLICATE" });
  });
});
