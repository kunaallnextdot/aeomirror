/* MonitorDetail — focused test for the AI Crawler Access panel's copy fix: "not
   blocked from fetching" must never be presented as confirmed AI-platform
   ingestion. Same conventions as AiTraffic.test.jsx (api.js mocked, no jest-dom). */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  getMonitor: vi.fn(),
  getMonitorHistory: vi.fn(),
  runMonitor: vi.fn(),
  updateMonitor: vi.fn(),
  deleteMonitor: vi.fn(),
  acknowledgeAlert: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));

import { getMonitor, getMonitorHistory } from "../api.js";
import MonitorDetail from "./MonitorDetail.jsx";

const MONITOR = {
  id: "m1", name: "Acme", domain: "acme.example", url: "https://acme.example/",
  status: "active", frequency: "weekly", latest_score: 80, last_scan_at: null,
  next_scan_at: null, history_count: 3, latest_scan_id: "s1",
};

beforeEach(() => {
  vi.clearAllMocks();
  getMonitorHistory.mockResolvedValue(null);
});

describe("MonitorDetail — AI Crawler Access panel", () => {
  it("never claims a 'not blocked' bot was actually read/ingested by an AI platform", async () => {
    getMonitor.mockResolvedValue({
      monitor: MONITOR, alerts: [], trends: {}, latest_changes: null, change_set: null,
      crawler_access: {
        site_unreachable: false,
        bots: [{ key: "gptbot", name: "GPTBot", provider: "OpenAI", critical: true, status: "allowed" }],
        findings: [],
      },
    });
    render(<MonitorDetail monitorId="m1" onBack={() => {}} onOpenReport={() => {}} />);
    await waitFor(() => screen.getByText("Not blocked"));
    // The old overclaiming label must be gone.
    expect(screen.queryByText("Allowed")).toBeNull();
    expect(screen.queryByText(/can the ai crawlers you need actually read this site/i)).toBeNull();
    // The honest clarifying note is present.
    screen.getByText(/not confirmation that an ai platform has actually used this page/i);
  });

  it("still shows a real block as blocked, not softened", async () => {
    getMonitor.mockResolvedValue({
      monitor: MONITOR, alerts: [], trends: {}, latest_changes: null, change_set: null,
      crawler_access: {
        site_unreachable: false,
        bots: [{ key: "gptbot", name: "GPTBot", provider: "OpenAI", critical: true, status: "blocked_by_robots" }],
        findings: [{ bot: "gptbot", bot_name: "GPTBot", remediation: "Allow GPTBot in robots.txt." }],
      },
    });
    render(<MonitorDetail monitorId="m1" onBack={() => {}} onOpenReport={() => {}} />);
    await waitFor(() => screen.getByText(/blocked · robots\.txt/i));
  });
});
