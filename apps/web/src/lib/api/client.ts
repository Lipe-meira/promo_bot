import { tokenIdentity } from "./token";
import type {
  AgentAction,
  AgentStatus,
  Capabilities,
  ErrorEnvelope,
  Me,
  PairingCode,
  TelegramChannels,
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
/** The session an operation began under is gone (sign-out, replacement, account switch, unmount). */
export const SESSION_CHANGED = "SESSION_CHANGED";

/**
 * The Clerk session a client is bound to, fixed when it was created.
 *
 * Invariants (each one is covered by tests):
 * - `userId` and `sessionId` never change; every token an operation obtains must carry exactly this
 *   `sub` and this `sid`, so a token of another user *or of another session of the same user* is
 *   never sent.
 * - `signal()` is read once, when an operation starts, and is aborted when the session ends.
 * - `isActive()` is a *live* read of the effective Clerk state (authentication loaded, same user,
 *   same session, session still active). It does not depend on React having re-rendered, so it
 *   closes the window between Clerk ending/replacing a session and the tree catching up. It must be
 *   cheap and synchronous: it is called immediately before the request leaves.
 */
export interface ApiSession {
  readonly userId: string;
  readonly sessionId: string;
  readonly getToken: TokenGetter;
  readonly signal: () => AbortSignal;
  readonly isActive: () => boolean;
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
  getTelegramChannels(id: string): Promise<TelegramChannels>;
  addSourceChannel(id: string, chatId: string): Promise<TelegramChannels>;
  removeSourceChannel(id: string, chatId: string): Promise<TelegramChannels>;
  setDestinationChannel(id: string, chatId: string): Promise<TelegramChannels>;
  getAgent(id: string): Promise<AgentStatus>;
  createPairingCode(id: string): Promise<PairingCode>;
  removeAgent(id: string): Promise<AgentStatus>;
  sendAgentCommand(id: string, action: AgentAction): Promise<AgentStatus>;
}

/**
 * Typed HTTP client. It holds no state and no cache: every call asks Clerk for a fresh session
 * token and goes to the network with `cache: "no-store"`. Authorization is the API's decision;
 * nothing here sends a user id or a role.
 *
 * Each call is bound to the Clerk `session` it started under. Cancelling a request that already
 * left does not undo it on the server, so the guarantee that matters is the one *before* sending:
 * after the (asynchronous) token arrives, and with no `await` between the last check and `fetch`,
 * the call verifies that its session is still the live, active one (`isActive`, which does not wait
 * for React) and that the token carries the same `sub` *and* `sid`; otherwise nothing is sent.
 */
export function createApiClient(
  baseUrl: string,
  session: ApiSession,
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): ApiClient {
  const root = baseUrl.replace(/\/+$/, "");

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const signal = session.signal(); // the session this operation belongs to
    if (signal.aborted || !session.isActive()) throw sessionChanged();

    const token = await session.getToken();

    // Everything from here to `fetch` is synchronous: what is verified is what is sent.
    // The token may have been issued for whoever (and whichever session) is active *now*, and the
    // session this operation began under may already be over even though React still renders it.
    if (signal.aborted || !session.isActive()) throw sessionChanged();
    if (!token) {
      throw new ApiClientError(401, "AUTH_INVALID_TOKEN", "Sua sessão expirou. Entre novamente.");
    }
    const identity = tokenIdentity(token);
    if (identity === null) {
      throw new ApiClientError(401, "AUTH_INVALID_TOKEN", "Sua sessão não é mais válida.");
    }
    if (identity.subject !== session.userId || identity.sessionId !== session.sessionId) {
      throw sessionChanged();
    }

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
      if (signal.aborted || !session.isActive()) throw sessionChanged();
      throw new ApiClientError(0, NETWORK_ERROR, "Não foi possível falar com o servidor.");
    }

    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    // An answer that comes back after the session ended or was replaced is never handed to the
    // (new) screen, whether or not anything aborted the request.
    if (signal.aborted || !session.isActive()) throw sessionChanged();
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
    getTelegramChannels: (id) =>
      request<TelegramChannels>("GET", `${workspace(id)}/telegram-channels`),
    addSourceChannel: (id, chatId) =>
      request<TelegramChannels>("POST", `${workspace(id)}/telegram-channels/sources`, {
        chat_id: chatId,
      }),
    removeSourceChannel: (id, chatId) =>
      request<TelegramChannels>(
        "DELETE",
        `${workspace(id)}/telegram-channels/sources/${encodeURIComponent(chatId)}`,
      ),
    setDestinationChannel: (id, chatId) =>
      request<TelegramChannels>("PUT", `${workspace(id)}/telegram-channels/destination`, {
        chat_id: chatId,
      }),
    getAgent: (id) => request<AgentStatus>("GET", `${workspace(id)}/agent`),
    createPairingCode: (id) => request<PairingCode>("POST", `${workspace(id)}/agent/pairing-code`),
    removeAgent: (id) => request<AgentStatus>("DELETE", `${workspace(id)}/agent`),
    sendAgentCommand: (id, action) =>
      request<AgentStatus>("POST", `${workspace(id)}/agent/commands`, { action }),
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
  "GET /v1/workspaces/{workspace_id}/telegram-channels",
  "POST /v1/workspaces/{workspace_id}/telegram-channels/sources",
  "DELETE /v1/workspaces/{workspace_id}/telegram-channels/sources/{chat_id}",
  "PUT /v1/workspaces/{workspace_id}/telegram-channels/destination",
  "GET /v1/workspaces/{workspace_id}/agent",
  "DELETE /v1/workspaces/{workspace_id}/agent",
  "POST /v1/workspaces/{workspace_id}/agent/pairing-code",
  "POST /v1/workspaces/{workspace_id}/agent/commands",
] as const;
