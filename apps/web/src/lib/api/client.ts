import { tokenSubject } from "./token";
import type {
  Capabilities,
  ErrorEnvelope,
  Me,
  Workspace,
  WorkspaceCreate,
  WorkspaceStatus,
  WorkspaceUpdate,
} from "./types";

export type TokenGetter = () => Promise<string | null>;

/** Stable codes of the API plus the two the client itself can produce. */
export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export const NETWORK_ERROR = "NETWORK_ERROR";
export const UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE";
/** The session an operation began under is gone (sign-out, account switch, unmount). */
export const SESSION_CHANGED = "SESSION_CHANGED";

/**
 * The identity and lifetime a client is bound to. An operation captures `signal()` when it starts
 * and is dropped, before any request is sent, if that session ends or if the token it obtains
 * belongs to anyone but `userId`.
 */
export interface ApiSession {
  /** Clerk user the client acts for; every token obtained must have been issued to this user. */
  readonly userId: string;
  readonly getToken: TokenGetter;
  /** Aborted when the session ends. Read once, when an operation starts. */
  readonly signal: () => AbortSignal;
}

const sessionChanged = () =>
  new ApiClientError(0, SESSION_CHANGED, "Sua sessão mudou. Repita a ação.");

function isEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

export interface ApiClient {
  me(): Promise<Me>;
  createWorkspace(body: WorkspaceCreate): Promise<Workspace>;
  getWorkspace(id: string): Promise<Workspace>;
  updateWorkspace(id: string, body: WorkspaceUpdate): Promise<Workspace>;
  getStatus(id: string): Promise<WorkspaceStatus>;
  getCapabilities(id: string): Promise<Capabilities>;
}

/**
 * Typed HTTP client. It holds no state and no cache: every call asks Clerk for a fresh session
 * token and goes to the network with `cache: "no-store"`. Authorization is the API's decision;
 * nothing here sends a user id or a role.
 *
 * Each call is bound to the `session` it started under. Cancelling a request that already left
 * does not undo it on the server, so the guarantee that matters is the one *before* sending: after
 * the (asynchronous) token arrives, the call re-checks that its session is still alive and that
 * the token was issued to the same user, and otherwise nothing is sent.
 */
export function createApiClient(
  baseUrl: string,
  session: ApiSession,
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): ApiClient {
  const root = baseUrl.replace(/\/+$/, "");

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const signal = session.signal(); // the session this operation belongs to
    if (signal.aborted) throw sessionChanged();

    const token = await session.getToken();
    // The token may have been obtained for whoever is signed in *now*, not for the person who
    // started this operation: nothing is sent unless both still match.
    if (signal.aborted) throw sessionChanged();
    if (!token) {
      throw new ApiClientError(401, "AUTH_INVALID_TOKEN", "Sua sessão expirou. Entre novamente.");
    }
    const subject = tokenSubject(token);
    if (subject === null) {
      throw new ApiClientError(401, "AUTH_INVALID_TOKEN", "Sua sessão não é mais válida.");
    }
    if (subject !== session.userId) throw sessionChanged();

    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let response: Response;
    try {
      response = await fetchImpl(`${root}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        credentials: "omit",
        signal,
      });
    } catch {
      if (signal.aborted) throw sessionChanged();
      throw new ApiClientError(0, NETWORK_ERROR, "Não foi possível falar com o servidor.");
    }

    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    // An answer that comes back after the session ended is never handed to the (new) screen.
    if (signal.aborted) throw sessionChanged();
    if (!response.ok) {
      if (isEnvelope(payload)) {
        const { code, message, details } = payload.error;
        throw new ApiClientError(response.status, code, message, details ?? null);
      }
      throw new ApiClientError(response.status, UNEXPECTED_RESPONSE, "Resposta inesperada do servidor.");
    }
    if (payload === null) {
      throw new ApiClientError(response.status, UNEXPECTED_RESPONSE, "Resposta inesperada do servidor.");
    }
    return payload as T;
  }

  const workspace = (id: string) => `/v1/workspaces/${encodeURIComponent(id)}`;
  return {
    me: () => request<Me>("GET", "/v1/me"),
    createWorkspace: (body) => request<Workspace>("POST", "/v1/workspaces", body),
    getWorkspace: (id) => request<Workspace>("GET", workspace(id)),
    updateWorkspace: (id, body) => request<Workspace>("PATCH", workspace(id), body),
    getStatus: (id) => request<WorkspaceStatus>("GET", `${workspace(id)}/status`),
    getCapabilities: (id) => request<Capabilities>("GET", `${workspace(id)}/capabilities`),
  };
}

/** Operations the client calls, as "METHOD path-template"; checked against the OpenAPI contract. */
export const USED_OPERATIONS = [
  "GET /v1/me",
  "POST /v1/workspaces",
  "GET /v1/workspaces/{workspace_id}",
  "PATCH /v1/workspaces/{workspace_id}",
  "GET /v1/workspaces/{workspace_id}/status",
  "GET /v1/workspaces/{workspace_id}/capabilities",
] as const;
