import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { USED_OPERATIONS } from "./client";
import { describeError } from "./messages";
import { ApiClientError } from "./client";

interface Operation {
  responses: Record<string, unknown>;
  security?: unknown[];
}
interface Document {
  paths: Record<string, Record<string, Operation>>;
  components: { schemas: Record<string, { properties?: Record<string, unknown>; required?: string[] }> };
}

// The backend writes this file from its pydantic models (`python -m promo_api.openapi`).
const contract: Document = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../../api/openapi.json"), "utf8"),
);

describe("frontend/backend contract", () => {
  it("every operation the client calls exists in the OpenAPI document", () => {
    for (const operation of USED_OPERATIONS) {
      const [method, route] = operation.split(" ") as [string, string];
      expect(contract.paths[route]?.[method.toLowerCase()], operation).toBeDefined();
    }
  });

  it("every /v1 operation is bearer-protected and the client covers all of them", () => {
    const documented = Object.entries(contract.paths)
      .filter(([route]) => route.startsWith("/v1/"))
      .flatMap(([route, item]) =>
        Object.entries(item).map(([method, operation]) => {
          expect(operation.security, `${method} ${route}`).toEqual([{ HTTPBearer: [] }]);
          return `${method.toUpperCase()} ${route}`;
        }),
      );

    expect(new Set(documented)).toEqual(new Set(USED_OPERATIONS));
  });

  it("the update contract needs a version and refuses unknown fields", () => {
    const update = contract.components.schemas.WorkspaceUpdate!;

    expect(update.required).toEqual(["version"]);
    expect(Object.keys(update.properties ?? {}).sort()).toEqual(["name", "timezone", "version"]);
    expect((update as { additionalProperties?: boolean }).additionalProperties).toBe(false);
  });

  it("the client shows a friendly message for every error code the contract can produce", () => {
    // Codes raised by the API (see promo_api); a new one must get a message or this fails.
    const codes = [
      "AUTH_INVALID_TOKEN",
      "AUTH_TOKEN_EXPIRED",
      "AUTH_KEYS_UNAVAILABLE",
      "NOT_ADMITTED",
      "WORKSPACE_NOT_FOUND",
      "WORKSPACE_ALREADY_EXISTS",
      "WORKSPACE_VERSION_CONFLICT",
      "VALIDATION_ERROR",
      "NOT_READY",
      "SOURCE_CHANNEL_DUPLICATE",
      "SOURCE_CHANNEL_LIMIT",
      "SOURCE_CHANNEL_NOT_FOUND",
      "CHANNEL_IS_DESTINATION",
      "DESTINATION_IS_SOURCE",
    ];
    for (const code of codes) {
      expect(describeError(new ApiClientError(400, code, "raw"))).not.toBe(
        "Algo deu errado. Tente novamente.",
      );
    }
  });
});
