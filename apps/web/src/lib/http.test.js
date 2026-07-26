import { describe, it, expect, beforeEach } from "vitest";
import { pretty, auth401 } from "./http.js";
import { session } from "../store/session.js";

const BASE = {
  authenticated: true,
  username: "op",
  auth_required: true,
  password_login_enabled: true,
  users_configured: true,
  checked: true,
};

beforeEach(() => {
  session.value = { ...BASE };
});

describe("pretty", () => {
  it("passes strings through unchanged", () => {
    expect(pretty("hi")).toBe("hi");
    expect(pretty("")).toBe("");
  });
  it("pretty-prints objects with 2-space indent", () => {
    expect(pretty({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
  it("falls back to String() on circular refs", () => {
    const o = {};
    o.self = o;
    expect(pretty(o)).toBe("[object Object]");
  });
});

describe("auth401", () => {
  it("returns false and leaves session alone for non-401", () => {
    expect(auth401({ status: 500 })).toBe(false);
    expect(session.value.authenticated).toBe(true);
  });
  it("returns false for null/undefined", () => {
    expect(auth401(null)).toBe(false);
    expect(auth401(undefined)).toBe(false);
    expect(session.value.authenticated).toBe(true);
  });
  it("returns true and clears authenticated on 401", () => {
    expect(auth401({ status: 401 })).toBe(true);
    expect(session.value.authenticated).toBe(false);
  });
  it("keeps the rest of the session fields intact", () => {
    auth401({ status: 401 });
    expect(session.value.username).toBe("op");
    expect(session.value.checked).toBe(true);
  });
});
