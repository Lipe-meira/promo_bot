import { describe, expect, it, vi } from "vitest";

import { ApiClientError, createApiClient, NETWORK_ERROR, UNEXPECTED_RESPONSE } from "./client";
import { admissionReason, describeError, invalidFields } from "./messages";

const ID = "11111111-1111-4111-8111-111111111111";

function reply(status: number, body: unknown) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
}

function clientWith(fetchImpl: typeof fetch, token: string | null = "tok") {
  return createApiClient("http://api.test/", async () => token, fetchImpl);
}

describe("request shape", () => {
  it("sends only the bearer token: no cache, no cookies, no identity or role", async () => {
    const fetchMock = vi.fn(async () => reply(200, { user: {}, workspaces: [] }));

    await clientWith(fetchMock as unknown as typeof fetch).me();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/v1/me");
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("omit");
    expect(init.headers).toEqual({ Accept: "application/json", Authorization: "Bearer tok" });
  });

  it("asks Clerk for a fresh token on every call", async () => {
    const getToken = vi.fn().mockResolvedValueOnce("first").mockResolvedValueOnce("second");
    const seen: string[] = [];
    const fetchMock = async (_url: string, init: RequestInit) => {
      seen.push((init.headers as Record<string, string>).Authorization!);
      return reply(200, { user: {}, workspaces: [] });
    };
    const api = createApiClient("http://api.test", getToken, fetchMock as unknown as typeof fetch);

    await api.me();
    await api.me();

    expect(seen).toEqual(["Bearer first", "Bearer second"]);
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
