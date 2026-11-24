import { describe, expect, it } from "vitest";

import { tokenFor } from "@/test/tokens";

import { tokenIdentity } from "./token";

const encode = (claims: unknown) =>
  `h.${btoa(JSON.stringify(claims)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}.s`;

describe("tokenIdentity", () => {
  it("reads the user (sub) and the Clerk session (sid)", () => {
    expect(tokenIdentity(tokenFor("user_a", { sid: "sess_1" }))).toEqual({
      subject: "user_a",
      sessionId: "sess_1",
    });
  });

  it("tells two sessions of the same user apart", () => {
    const first = tokenIdentity(tokenFor("user_a", { sid: "sess_1" }));
    const second = tokenIdentity(tokenFor("user_a", { sid: "sess_2" }));

    expect(first?.subject).toBe(second?.subject);
    expect(first?.sessionId).not.toBe(second?.sessionId);
  });

  it.each([
    ["not a JWT", "opaque"],
    ["two segments", "a.b"],
    ["empty payload", "a..c"],
    ["undecodable payload", "a.!!!.c"],
    ["payload that is not JSON", "a.bm90LWpzb24.c"],
    ["JSON null", encode(null)],
    ["no sub", encode({ sid: "sess_1" })],
    ["empty sub", encode({ sub: "", sid: "sess_1" })],
    ["non-string sub", encode({ sub: 7, sid: "sess_1" })],
    ["no sid", encode({ sub: "user_a" })],
    ["empty sid", encode({ sub: "user_a", sid: "" })],
    ["non-string sid", encode({ sub: "user_a", sid: 7 })],
  ])("returns null when the identity cannot be read (%s)", (_name, token) => {
    expect(tokenIdentity(token)).toBeNull();
  });
});
