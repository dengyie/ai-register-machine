import { describe, it, expect } from "vitest";
import {
  normalizeProviderName,
  normalizeProvidersList,
  providersFromConfig,
  EMAIL_PROVIDERS,
} from "./providers.js";

describe("normalizeProviderName", () => {
  it("maps outlook aliases to hotmail", () => {
    expect(normalizeProviderName("Outlook")).toBe("hotmail");
    expect(normalizeProviderName("microsoft")).toBe("hotmail");
    expect(normalizeProviderName(" outlookmail ")).toBe("hotmail");
  });
  it("maps google aliases to gmail", () => {
    expect(normalizeProviderName("GoogleMail")).toBe("gmail");
    expect(normalizeProviderName("google")).toBe("gmail");
  });
  it("returns empty string for blank input", () => {
    expect(normalizeProviderName(null)).toBe("");
    expect(normalizeProviderName("  ")).toBe("");
  });
});

describe("normalizeProvidersList", () => {
  it("parses comma / space / full-width separators", () => {
    expect(normalizeProvidersList("gmail, hotmail　cloudflare")).toEqual([
      "gmail",
      "hotmail",
      "cloudflare",
    ]);
  });
  it("dedupes and drops unknown", () => {
    expect(normalizeProvidersList(["gmail", "gmail", "bogus"])).toEqual(["gmail"]);
  });
  it("accepts arrays and preserves input order", () => {
    expect(normalizeProvidersList(EMAIL_PROVIDERS)).toEqual(EMAIL_PROVIDERS);
    expect(normalizeProvidersList(["hotmail", "cloudflare"])).toEqual([
      "hotmail",
      "cloudflare",
    ]);
  });
  it("normalizes aliases while deduping", () => {
    expect(normalizeProvidersList("outlook,hotmail,google")).toEqual([
      "hotmail",
      "gmail",
    ]);
  });
  it("empty in → empty out", () => {
    expect(normalizeProvidersList("")).toEqual([]);
    expect(normalizeProvidersList(null)).toEqual([]);
    expect(normalizeProvidersList([])).toEqual([]);
  });
});

describe("providersFromConfig", () => {
  it("prefers multi over singleton", () => {
    expect(
      providersFromConfig({ email_providers: ["gmail"], email_provider: "hotmail" }),
    ).toEqual(["gmail"]);
  });
  it("falls back to singleton when multi empty", () => {
    expect(
      providersFromConfig({ email_providers: [], email_provider: "hotmail" }),
    ).toEqual(["hotmail"]);
  });
  it("drops an unknown singleton and uses the fallback", () => {
    expect(
      providersFromConfig({ email_provider: "bogus" }, { fallback: ["yyds"] }),
    ).toEqual(["yyds"]);
  });
  it("returns a copy of the fallback, not the same array", () => {
    const fallback = ["yyds"];
    const out = providersFromConfig({}, { fallback });
    expect(out).toEqual(["yyds"]);
    expect(out).not.toBe(fallback);
  });
  it("tolerates null config", () => {
    expect(providersFromConfig(null)).toEqual([]);
  });
});
