// Regression tests locking MailTab's config-form invariants.
// Written BEFORE the useConfigForm extraction — these must stay green after it.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/preact";

vi.mock("../../api/client.js", () => ({
  getConfig: vi.fn(),
  putConfig: vi.fn(),
  mailPoolStats: vi.fn(),
  cloudflareDomains: vi.fn(),
  importMailText: vi.fn(),
  probeMail: vi.fn(),
  compactMail: vi.fn(),
  quarantineMail: vi.fn(),
}));

import * as api from "../../api/client.js";
import { session } from "../../store/session.js";
import { MailTab } from "./MailTab.jsx";

const LOADED = {
  config: {
    defaultDomains: "a.com,b.com",
    cloudflare_api_base: "https://cf.example",
  },
};

function saveBtn() {
  return screen.getByRole("button", { name: "保存邮箱配置" });
}
function reloadBtn() {
  return screen.getByRole("button", { name: "重载" });
}
function domainsInput() {
  return screen.getByLabelText(/defaultDomains（全局/);
}

beforeEach(() => {
  vi.clearAllMocks();
  session.value = { authenticated: true, checked: true };
  api.mailPoolStats.mockResolvedValue({ by_domain: {}, known_domains: [] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MailTab config-form invariants", () => {
  it("1. GET failure leaves Save disabled and sends no PUT", async () => {
    api.getConfig.mockRejectedValue(new Error("boom"));
    render(<MailTab />);
    await waitFor(() => expect(saveBtn().disabled).toBe(true));
    fireEvent.click(saveBtn());
    await waitFor(() => expect(api.putConfig).not.toHaveBeenCalled());
  });

  it("2. GET 401 clears the session and leaves Save disabled", async () => {
    const e = new Error("unauthorized");
    e.status = 401;
    api.getConfig.mockRejectedValue(e);
    render(<MailTab />);
    await waitFor(() => expect(session.value.authenticated).toBe(false));
    expect(saveBtn().disabled).toBe(true);
  });

  it("3. Reload while dirty prompts, and cancelling sends no second GET", async () => {
    api.getConfig.mockResolvedValue(LOADED);
    render(<MailTab />);
    await waitFor(() => expect(saveBtn().disabled).toBe(false));
    expect(api.getConfig).toHaveBeenCalledTimes(1);

    fireEvent.input(domainsInput(), { target: { value: "c.com" } });

    window.confirm.mockReturnValue(false);
    fireEvent.click(reloadBtn());
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(api.getConfig).toHaveBeenCalledTimes(1);
  });

  it("4. clearing defaultDomains prompts on Save; cancelling sends no PUT", async () => {
    api.getConfig.mockResolvedValue(LOADED);
    render(<MailTab />);
    await waitFor(() => expect(saveBtn().disabled).toBe(false));

    fireEvent.input(domainsInput(), { target: { value: "" } });

    window.confirm.mockReturnValue(false);
    fireEvent.click(saveBtn());
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(window.confirm.mock.calls[0][0]).toContain("defaultDomains");
    expect(api.putConfig).not.toHaveBeenCalled();
  });

  it("5. successful Save clears dirty and rehydrates domains from the server response", async () => {
    api.getConfig.mockResolvedValue(LOADED);
    api.putConfig.mockResolvedValue({
      config: { defaultDomains: "server.com" },
    });
    render(<MailTab />);
    await waitFor(() => expect(saveBtn().disabled).toBe(false));

    fireEvent.input(domainsInput(), { target: { value: "c.com,d.com" } });

    fireEvent.click(saveBtn());
    await waitFor(() => expect(api.putConfig).toHaveBeenCalledTimes(1));
    expect(api.putConfig.mock.calls[0][0].config.defaultDomains).toBe(
      "c.com,d.com",
    );

    // Server value wins after save; dirty is cleared so Reload won't prompt.
    await waitFor(() =>
      expect(domainsInput().value).toBe("server.com"),
    );
    window.confirm.mockClear();
    fireEvent.click(reloadBtn());
    await waitFor(() => expect(api.getConfig).toHaveBeenCalledTimes(2));
    expect(window.confirm).not.toHaveBeenCalled();
  });
});
