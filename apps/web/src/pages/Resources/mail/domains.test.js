import { describe, it, expect } from "vitest";
import { parseDomainList, joinDomains } from "./domains.js";

describe("parseDomainList", () => {
  it("splits on comma, full-width comma, and whitespace", () => {
    expect(parseDomainList("a.com，b.com c.com")).toEqual([
      "a.com",
      "b.com",
      "c.com",
    ]);
  });
  it("returns [] for empty/null", () => {
    expect(parseDomainList("")).toEqual([]);
    expect(parseDomainList(null)).toEqual([]);
  });
});

describe("joinDomains", () => {
  it("dedupes case-insensitively, keeping first spelling and order", () => {
    expect(joinDomains(["A.com", "b.com", "a.COM"])).toBe("A.com,b.com");
  });
  it("drops blanks", () => {
    expect(joinDomains(["", "  ", "a.com"])).toBe("a.com");
  });
  it("returns '' for empty/null", () => {
    expect(joinDomains(null)).toBe("");
    expect(joinDomains([])).toBe("");
  });
});
