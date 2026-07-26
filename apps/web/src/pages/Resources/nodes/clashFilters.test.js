import { describe, it, expect } from "vitest";
import { filterClashLeaves, namesInPool } from "./clashFilters.js";

const LEAVES = [
  { name: "hk-1", health: "fail", in_register_pool: true, last_delay_ms: 100, groups: ["🎯Grok注册"] },
  { name: "us-2", health: "ok", in_register_pool: true, last_delay_ms: 300, groups: ["🎯Grok注册"] },
  { name: "jp-3", health: "ok", in_register_pool: false, last_delay_ms: 50, groups: ["PROXY"] },
  { name: "sg-4", health: "unknown", in_register_pool: true, last_delay_ms: null, groups: [] },
];

describe("filterClashLeaves", () => {
  it("pool mode keeps only register-pool leaves", () => {
    expect(filterClashLeaves(LEAVES, "pool", "").map((n) => n.name)).toEqual([
      "us-2",
      "sg-4",
      "hk-1",
    ]);
  });
  it("sorts pool-first, then ok > unknown > fail, then by delay", () => {
    expect(filterClashLeaves(LEAVES, "all", "").map((n) => n.name)).toEqual([
      "us-2",
      "sg-4",
      "hk-1",
      "jp-3",
    ]);
  });
  it("query filters by case-insensitive substring", () => {
    expect(filterClashLeaves(LEAVES, "all", "JP").map((n) => n.name)).toEqual(["jp-3"]);
  });
  it("does not mutate the input array", () => {
    const before = LEAVES.map((n) => n.name);
    filterClashLeaves(LEAVES, "all", "");
    expect(LEAVES.map((n) => n.name)).toEqual(before);
  });
});

describe("namesInPool", () => {
  it("matches by group membership", () => {
    expect(namesInPool({ leaves: LEAVES }, "PROXY")).toEqual(["jp-3"]);
  });
  it("falls back to in_register_pool for known register groups", () => {
    expect(namesInPool({ leaves: LEAVES }, "♻️Grok优选")).toEqual([
      "hk-1",
      "us-2",
      "sg-4",
    ]);
  });
  it("honours the limit", () => {
    expect(namesInPool({ leaves: LEAVES }, "♻️Grok优选", 2)).toHaveLength(2);
  });
});
