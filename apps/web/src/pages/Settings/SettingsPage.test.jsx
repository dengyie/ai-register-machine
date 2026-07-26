// Regression tests locking SettingsPage's config-form invariants.
// Written BEFORE the useConfigForm extraction — these must stay green after it.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/preact";

vi.mock("../../api/client.js", () => ({
  getConfig: vi.fn(),
  putConfig: vi.fn(),
  selfcheck: vi.fn(),
  cleanupOrphans: vi.fn(),
  getToken: vi.fn(() => ""),
  setToken: vi.fn(),
}));

import * as api from "../../api/client.js";
import { session } from "../../store/session.js";
import { SettingsPage } from "./SettingsPage.jsx";

const LOADED = {
  config: {
    email_providers: ["hotmail", "gmail"],
    email_provider: "hotmail",
    email_provider_strategy: "round_robin",
    proxy: "http://p:1",
    proxy_list: "http://a:1\nhttp://b:2",
  },
};

function btn(name) {
  return screen.getByRole("button", { name });
}

beforeEach(() => {
  vi.clearAllMocks();
  session.value = { authenticated: true, checked: true };
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsPage config-form invariants", () => {
  it("1. GET failure leaves Save disabled and sends no PUT", async () => {
    api.getConfig.mockRejectedValue(new Error("boom"));
    render(<SettingsPage />);
    await waitFor(() => expect(btn("Save").disabled).toBe(true));
    fireEvent.click(btn("Save"));
    await waitFor(() => expect(api.putConfig).not.toHaveBeenCalled());
  });

  it("2. GET 401 clears the session and leaves Save disabled", async () => {
    const e = new Error("unauthorized");
    e.status = 401;
    api.getConfig.mockRejectedValue(e);
    render(<SettingsPage />);
    await waitFor(() => expect(session.value.authenticated).toBe(false));
    expect(btn("Save").disabled).toBe(true);
  });

  it("3. Reload while dirty prompts, and cancelling sends no second GET", async () => {
    api.getConfig.mockResolvedValue(LOADED);
    render(<SettingsPage />);
    await waitFor(() => expect(btn("Save").disabled).toBe(false));
    expect(api.getConfig).toHaveBeenCalledTimes(1);

    // Make the form dirty.
    fireEvent.input(screen.getByLabelText(/单条代理/), {
      target: { value: "http://changed:9" },
    });
    await screen.findByText("未保存");

    window.confirm.mockReturnValue(false);
    fireEvent.click(btn("Reload"));
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(api.getConfig).toHaveBeenCalledTimes(1);
  });

  it("4. clearing a clearable field prompts on Save; cancelling sends no PUT", async () => {
    api.getConfig.mockResolvedValue(LOADED);
    render(<SettingsPage />);
    await waitFor(() => expect(btn("Save").disabled).toBe(false));

    // Untick every provider chip -> clears the multi pool.
    for (const p of ["hotmail", "gmail"]) {
      fireEvent.click(screen.getByLabelText(p));
    }

    window.confirm.mockReturnValue(false);
    fireEvent.click(btn("Save"));
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(window.confirm.mock.calls[0][0]).toContain("email_providers");
    expect(api.putConfig).not.toHaveBeenCalled();
  });

  it("5. successful Save clears dirty and rehydrates proxy_list from the server response", async () => {
    api.getConfig.mockResolvedValue(LOADED);
    api.putConfig.mockResolvedValue({
      config: { ...LOADED.config, proxy_list: ["http://server-said:7"] },
    });
    render(<SettingsPage />);
    await waitFor(() => expect(btn("Save").disabled).toBe(false));

    fireEvent.input(screen.getByLabelText(/proxy_list/), {
      target: { value: "http://changed:9" },
    });
    await screen.findByText("未保存");

    fireEvent.click(btn("Save"));
    await waitFor(() => expect(api.putConfig).toHaveBeenCalledTimes(1));
    expect(api.putConfig.mock.calls[0][0].config.proxy_list).toBe(
      "http://changed:9",
    );

    await waitFor(() =>
      expect(screen.queryByText("未保存")).toBeNull(),
    );
    // Server multi-list / proxy_list win over the local edit after save.
    expect(screen.getByLabelText(/proxy_list/).value).toBe(
      "http://server-said:7",
    );
  });
});
