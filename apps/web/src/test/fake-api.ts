import { vi } from "vitest";

import type { Capability, Workspace } from "@/lib/api/types";

import { userOfAuthorization } from "./tokens";

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
  init: RequestInit;
}

type Reply = { status: number; body: unknown };
interface AgentState {
  paired: boolean;
  connected: boolean;
  process_state: "running" | "stopped" | "unknown";
  pending_action: "start" | "stop" | null;
  last_seen_at: string | null;
  last_event: "start_failed" | "exited" | null;
}
const NO_AGENT: AgentState = {
  paired: false,
  connected: false,
  process_state: "unknown",
  pending_action: null,
  last_seen_at: null,
  last_event: null,
};
interface ChannelState {
  sources: { chat_id: string; created_at: string }[];
  destination: { chat_id: string; updated_at: string } | null;
}
const CHAT_ID = /^-100[1-9][0-9]{0,14}$/;
const MAX_SOURCES = 50;
const json = (status: number, body: unknown): Reply => ({ status, body });
const error = (status: number, code: string, details?: Record<string, unknown>): Reply =>
  json(status, { error: { code, message: "server text that must never reach the screen", details } });

export const CAPABILITIES: Capability[] = [
  { key: "workspace_settings", title: "Configurações do workspace", available: true },
  {
    key: "telegram_connection",
    title: "Conexão com o Telegram",
    available: false,
    reason_code: "REQUIRES_SECRET_VAULT",
    reason: "O login por QR ainda não foi implementado.",
  },
  {
    key: "relay_automation",
    title: "Clonagem automática de promoções",
    available: false,
    reason_code: "REQUIRES_WORKER_SUPERVISOR",
    reason: "Os workers hospedados ainda não existem.",
  },
];

/** A stateful fake of the API, keyed by the token's user, to drive the screens end to end. */
export class FakeApi {
  calls: Call[] = [];
  admitted = new Set<string>(["user_a", "user_b"]);
  workspaces = new Map<string, Workspace>(); // by owner user
  /** Registered Telegram channels, by workspace id (the API scopes them the same way). */
  channels = new Map<string, ChannelState>();
  /** The local agent of each workspace, as the API would describe it. */
  agents = new Map<string, AgentState>();
  /** Answer reads of the agent with the state at arrival, not at reply (a slow poll). */
  snapshotReads = false;
  failNext: Reply | null = null;
  failPath: { match: RegExp; reply: Reply } | null = null;
  delayMs = 0;
  /** Lets a test hold a request in flight (after it was received, before it is answered). */
  beforeReply: ((request: { method: string; path: string; user: string | null }) => Promise<void>) | null =
    null;
  private counter = 0;

  install() {
    vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => this.handle(url, init));
    return this;
  }

  private async handle(url: string, init: RequestInit): Promise<Response> {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    this.calls.push({ method, path, headers, body, init });
    const early =
      this.snapshotReads && method === "GET" && path.endsWith("/agent")
        ? structuredClone(this.route(method, path, headers.Authorization ?? "", body))
        : null;
    if (this.delayMs) await new Promise((resolve) => setTimeout(resolve, this.delayMs));
    await this.beforeReply?.({ method, path, user: userOfAuthorization(headers.Authorization ?? "") });

    const reply = early ?? this.route(method, path, headers.Authorization ?? "", body);
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  private route(method: string, path: string, auth: string, body: any): Reply {
    if (this.failPath?.match.test(path)) return this.failPath.reply;
    if (this.failNext) {
      const reply = this.failNext;
      this.failNext = null;
      return reply;
    }
    const user = userOfAuthorization(auth);
    if (user === null) return error(401, "AUTH_INVALID_TOKEN");
    if (!this.admitted.has(user)) return error(403, "NOT_ADMITTED", { reason: "NO_INVITATION" });
    const own = this.workspaces.get(user);

    if (method === "GET" && path === "/v1/me") {
      return json(200, {
        user: { id: `id-${user}`, email: `${user}@example.test` },
        workspaces: own ? [{ id: own.id, name: own.name, role: "owner" }] : [],
      });
    }
    if (method === "POST" && path === "/v1/workspaces") {
      if (own) return error(409, "WORKSPACE_ALREADY_EXISTS", { workspace_id: own.id });
      const now = new Date().toISOString();
      const created: Workspace = {
        id: `00000000-0000-4000-8000-00000000000${++this.counter}`,
        name: body.name,
        timezone: body.timezone ?? "America/Sao_Paulo",
        version: 1,
        role: "owner",
        created_at: now,
        updated_at: now,
      };
      this.workspaces.set(user, created);
      return json(201, created);
    }
    const match =
      /^\/v1\/workspaces\/([^/]+)(\/status|\/capabilities|\/telegram-channels(?:\/sources(?:\/[^/]+)?|\/destination)?|\/agent(?:\/pairing-code|\/commands)?)?$/.exec(
        path,
      );
    if (!match || !own || own.id !== match[1]) return error(404, "WORKSPACE_NOT_FOUND");
    if (match[2]?.startsWith("/agent")) {
      return this.agentRoute(method, match[2].slice("/agent".length), own.id, body);
    }
    if (match[2]?.startsWith("/telegram-channels")) {
      return this.channelsRoute(method, match[2].slice("/telegram-channels".length), own.id, body);
    }
    if (method === "GET" && !match[2]) return json(200, own);
    if (method === "PATCH" && !match[2]) {
      if (body.version !== own.version) {
        return error(409, "WORKSPACE_VERSION_CONFLICT", { current_version: own.version });
      }
      const updated = {
        ...own,
        ...(body.name ? { name: body.name } : {}),
        ...(body.timezone ? { timezone: body.timezone } : {}),
        version: own.version + 1,
        updated_at: new Date().toISOString(),
      };
      this.workspaces.set(user, updated);
      return json(200, updated);
    }
    if (method === "GET" && match[2] === "/status") {
      const off = (state: string, reason: string) => ({ state, reason, last_heartbeat_at: null });
      return json(200, {
        workspace_id: own.id,
        operational: false,
        worker: off("not_provisioned", "Nenhum worker foi provisionado."),
        telegram: off("not_connected", "Nenhuma sessão do Telegram está conectada."),
        aliexpress: off("not_configured", "Nenhuma credencial AliExpress foi configurada."),
        checked_at: new Date().toISOString(),
      });
    }
    if (method === "GET" && match[2] === "/capabilities") {
      return json(200, { workspace_id: own.id, capabilities: CAPABILITIES });
    }
    return error(404, "NOT_FOUND");
  }

  /** Replies like the API for the manual channel registry (same id rule, same stable codes). */
  private channelsRoute(method: string, rest: string, workspaceId: string, body: any): Reply {
    const state = this.channels.get(workspaceId) ?? { sources: [], destination: null };
    this.channels.set(workspaceId, state);
    const view = (status = 200) =>
      json(status, {
        workspace_id: workspaceId,
        sources: state.sources,
        destination: state.destination,
        max_sources: MAX_SOURCES,
        access_status: "not_verified",
      });
    const invalid = () =>
      error(422, "VALIDATION_ERROR", { fields: [{ field: "chat_id", type: "string_pattern_mismatch" }] });
    const valid = (value: unknown): value is string => typeof value === "string" && CHAT_ID.test(value);
    const now = new Date().toISOString();

    if (method === "GET" && rest === "") return view();
    if (method === "POST" && rest === "/sources") {
      if (!valid(body?.chat_id)) return invalid();
      if (state.destination?.chat_id === body.chat_id) return error(409, "CHANNEL_IS_DESTINATION");
      if (state.sources.some((item) => item.chat_id === body.chat_id)) {
        return error(409, "SOURCE_CHANNEL_DUPLICATE");
      }
      if (state.sources.length >= MAX_SOURCES) {
        return error(409, "SOURCE_CHANNEL_LIMIT", { max_sources: MAX_SOURCES });
      }
      state.sources.push({ chat_id: body.chat_id, created_at: now });
      return view(201);
    }
    if (method === "DELETE" && rest.startsWith("/sources/")) {
      const chatId = decodeURIComponent(rest.slice("/sources/".length));
      if (!valid(chatId)) return invalid();
      const index = state.sources.findIndex((item) => item.chat_id === chatId);
      if (index < 0) return error(404, "SOURCE_CHANNEL_NOT_FOUND");
      state.sources.splice(index, 1);
      return view();
    }
    if (method === "PUT" && rest === "/destination") {
      if (!valid(body?.chat_id)) return invalid();
      if (state.sources.some((item) => item.chat_id === body.chat_id)) {
        return error(409, "DESTINATION_IS_SOURCE");
      }
      if (state.destination?.chat_id !== body.chat_id) {
        state.destination = { chat_id: body.chat_id, updated_at: now };
      }
      return view();
    }
    return error(404, "NOT_FOUND");
  }

  setAgent(workspaceId: string, changes: Partial<AgentState>) {
    this.agents.set(workspaceId, { ...(this.agents.get(workspaceId) ?? NO_AGENT), ...changes });
  }

  /** Replies like the API for the local agent (same codes, same idempotence). */
  private agentRoute(method: string, rest: string, workspaceId: string, body: any): Reply {
    const state = this.agents.get(workspaceId) ?? NO_AGENT;
    const view = () =>
      json(200, { workspace_id: workspaceId, ...(this.agents.get(workspaceId) ?? NO_AGENT) });
    if (method === "GET" && rest === "") return view();
    if (method === "DELETE" && rest === "") {
      this.agents.delete(workspaceId);
      return view();
    }
    if (method === "POST" && rest === "/pairing-code") {
      const expires_at = new Date(Date.now() + 600_000).toISOString();
      return json(201, { code: "ABCD-EFGH-JKMN", expires_at });
    }
    if (method === "POST" && rest === "/commands") {
      if (!state.paired) return error(409, "AGENT_NOT_PAIRED");
      if (!state.connected) return error(409, "AGENT_OFFLINE");
      if (state.pending_action === "stop" && body.action === "start") {
        return error(409, "AGENT_STOPPING");
      }
      const target = body.action === "start" ? "running" : "stopped";
      if (state.pending_action !== null || state.process_state !== target) {
        this.setAgent(workspaceId, { pending_action: body.action });
      }
      return view();
    }
    return error(404, "NOT_FOUND");
  }

  seedChannels(workspaceId: string, sources: string[], destination: string | null = null) {
    const now = new Date().toISOString();
    this.channels.set(workspaceId, {
      sources: sources.map((chat_id) => ({ chat_id, created_at: now })),
      destination: destination ? { chat_id: destination, updated_at: now } : null,
    });
  }

  seedWorkspace(user: string, name = "Loja da Alice"): Workspace {
    const now = new Date().toISOString();
    const workspace: Workspace = {
      id: `00000000-0000-4000-8000-00000000000${++this.counter}`,
      name,
      timezone: "America/Sao_Paulo",
      version: 1,
      role: "owner",
      created_at: now,
      updated_at: now,
    };
    this.workspaces.set(user, workspace);
    return workspace;
  }
}

export { error as errorReply, json as jsonReply };
