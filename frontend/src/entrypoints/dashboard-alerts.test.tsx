import { afterEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { renderWithClient, screen, waitFor } from "../utils/test-utils";
import { DashboardAlerts } from "./dashboard-alerts";

type ProviderProfileResponse = {
  profile_id: string;
  enabled: boolean;
  launch_ready: boolean;
};

function mockFetch(
  secrets: Array<{ slug: string; status: string }>,
  profiles: ProviderProfileResponse[] = [],
  profilesFail = false,
): MockInstance {
  return vi
    .spyOn(window, "fetch")
    .mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/secrets") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: secrets }),
        } as Response);
      }
      if (url === "/api/v1/provider-profiles") {
        return Promise.resolve({
          ok: !profilesFail,
          json: async () => profiles,
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404 } as Response);
    });
}

describe("DashboardAlerts", () => {
  let fetchSpy: MockInstance;

  afterEach(() => fetchSpy?.mockRestore());

  it("shows generic provider-profile and GitHub guidance when both are missing", async () => {
    fetchSpy = mockFetch([], []);
    renderWithClient(<DashboardAlerts />);

    expect(await screen.findByText(/First-Run Setup:/i)).toBeTruthy();
    expect(
      screen.getByText(
        "Set up and enable at least one provider profile in Settings.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Set up GitHub access in Settings.")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(
      /ANTHROPIC_API_KEY|OPENAI_API_KEY|MINIMAX_API_KEY/,
    );
  });

  it.each(["oauth", "api-backed"])(
    "does not warn for a launch-ready %s profile with GitHub access",
    async (kind) => {
      fetchSpy = mockFetch(
        [{ slug: "GITHUB_TOKEN", status: "active" }],
        [{ profile_id: kind, enabled: true, launch_ready: true }],
      );
      renderWithClient(<DashboardAlerts />);
      await waitFor(() =>
        expect(screen.queryByText(/First-Run Setup:/i)).toBeNull(),
      );
    },
  );

  it("does not treat an active provider secret as a ready profile", async () => {
    fetchSpy = mockFetch(
      [
        { slug: "OPENAI_API_KEY", status: "active" },
        { slug: "GITHUB_TOKEN", status: "active" },
      ],
      [],
    );
    renderWithClient(<DashboardAlerts />);
    expect(
      await screen.findByText(
        /Set up and enable at least one provider profile/i,
      ),
    ).toBeTruthy();
  });

  it.each([
    { profile_id: "disabled", enabled: false, launch_ready: false },
    { profile_id: "blocked", enabled: true, launch_ready: false },
  ])("warns for a non-ready profile", async (profile) => {
    fetchSpy = mockFetch([{ slug: "GITHUB_PAT", status: "active" }], [profile]);
    renderWithClient(<DashboardAlerts />);
    expect(
      await screen.findByText(
        /Set up and enable at least one provider profile/i,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/Set up GitHub access/i)).toBeNull();
  });

  it("shows only GitHub guidance when a provider is ready", async () => {
    fetchSpy = mockFetch(
      [],
      [{ profile_id: "ready", enabled: true, launch_ready: true }],
    );
    renderWithClient(<DashboardAlerts />);
    expect(await screen.findByText(/Set up GitHub access/i)).toBeTruthy();
    expect(
      screen.queryByText(/Set up and enable at least one provider profile/i),
    ).toBeNull();
  });

  it("renders a neutral notice when provider readiness cannot be checked", async () => {
    fetchSpy = mockFetch([], [], true);
    renderWithClient(<DashboardAlerts />);
    expect(
      await screen.findByText(/could not verify provider profile readiness/i),
    ).toBeTruthy();
    expect(
      screen.queryByText(/Set up and enable at least one provider profile/i),
    ).toBeNull();
    expect(document.body.textContent).not.toMatch(/API key/i);
  });
});
