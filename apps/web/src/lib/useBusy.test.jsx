import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/preact";
import { useBusy } from "./useBusy.js";

function Probe({ onReady }) {
  const b = useBusy();
  onReady(b);
  return <span data-testid="busy">{`${b.busy}|${b.is("save")}|${b.any}`}</span>;
}

describe("useBusy", () => {
  it("starts idle", () => {
    render(<Probe onReady={() => {}} />);
    expect(screen.getByTestId("busy").textContent).toBe("|false|false");
  });

  it("sets tag during run and clears after", async () => {
    let api;
    render(
      <Probe
        onReady={(b) => {
          api = b;
        }}
      />,
    );
    let release;
    const p = api.run(
      "save",
      () =>
        new Promise((r) => {
          release = r;
        }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("busy").textContent).toBe("save|true|true"),
    );
    release("done");
    await expect(p).resolves.toBe("done");
    await waitFor(() =>
      expect(screen.getByTestId("busy").textContent).toBe("|false|false"),
    );
  });

  it("clears the tag and rethrows when fn throws", async () => {
    let api;
    render(
      <Probe
        onReady={(b) => {
          api = b;
        }}
      />,
    );
    await expect(
      api.run("save", async () => {
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");
    await waitFor(() =>
      expect(screen.getByTestId("busy").textContent).toBe("|false|false"),
    );
  });
});
