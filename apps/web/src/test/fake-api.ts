import { vi } from "vitest";

import type { Capability, Workspace } from "@/lib/api/types";

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
  init: RequestInit;
}

type Reply = { status: number; body: unknown };
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
  failNext: Reply | null = null;
  failPath: { match: RegExp; reply: Reply } | null = null;
  delayMs = 0;
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
    if (this.delayMs) await new Promise((resolve) => setTimeout(resolve, this.delayMs));

    const reply = this.route(method, path, headers.Authorization ?? "", body);
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
    const user = auth.replace("Bearer token-for-", "");
    if (!auth.startsWith("Bearer token-for-")) return error(401, "AUTH_INVALID_TOKEN");
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
    const match = /^\/v1\/workspaces\/([^/]+)(\/status|\/capabilities)?$/.exec(path);
    if (!match || !own || own.id !== match[1]) return error(404, "WORKSPACE_NOT_FOUND");
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
