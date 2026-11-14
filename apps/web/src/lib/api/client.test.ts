import { describe, expect, it, vi } from "vitest";

import { tokenFor } from "@/test/tokens";

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

function sessionFor(
  getToken: TokenGetter,
  controller = new AbortController(),
  userId = "user_a",
): ApiSession {
  return { userId, getToken, signal: () => controller.signal };
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
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ok());
    const api = createApiClient(
      "http://api.test",
      sessionFor(getToken, controller, userId),
      fetchMock as unknown as typeof fetch,
    );
    return { controller, fetchMock, api };
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
  ])("fails closed on a token whose owner cannot be read (%s)", async (_name, token) => {
    const { fetchMock, api } = setup(async () => token);

    await expect(api.me()).rejects.toMatchObject({ code: "AUTH_INVALID_TOKEN" });
    expect(fetchMock).not.toHaveBeenCalled();
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
