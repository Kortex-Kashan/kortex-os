/**
 * ScheduleManager interaction tests (M5-A7).
 *
 * Closes a real M5.6 testing gap: this component previously had zero
 * dedicated tests, which is exactly why "Create Schedule" was guaranteed to
 * throw a backend `TypeError` on every real submission (M56-5) and every
 * Pause/Resume/Cancel/Trigger button sent `schedule_id: undefined` (M56-4) —
 * nothing ever clicked them.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  listSchedulesMock,
  createScheduleMock,
  pauseScheduleMock,
  cancelScheduleMock,
  triggerScheduleNowMock,
} = vi.hoisted(() => ({
  listSchedulesMock: vi.fn(),
  createScheduleMock: vi.fn(),
  pauseScheduleMock: vi.fn(),
  cancelScheduleMock: vi.fn(),
  triggerScheduleNowMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listSchedules: listSchedulesMock,
    createSchedule: createScheduleMock,
    pauseSchedule: pauseScheduleMock,
    cancelSchedule: cancelScheduleMock,
    triggerScheduleNow: triggerScheduleNowMock,
  };
});

import { ScheduleManager } from "./ScheduleManager";

const activeSchedule = {
  id: "sched-1",
  name: "daily-sync",
  definitionId: "wf-1",
  scheduleType: "CRON" as const,
  cronExpression: "0 9 * * *",
  intervalSeconds: null,
  nextRunAt: "2026-01-02T09:00:00Z",
  status: "ACTIVE" as const,
  runCount: 3,
  tenantId: "acme",
};

function renderManager() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ScheduleManager />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listSchedulesMock.mockReset();
  createScheduleMock.mockReset();
  pauseScheduleMock.mockReset();
  cancelScheduleMock.mockReset();
  triggerScheduleNowMock.mockReset();
});

describe("ScheduleManager", () => {
  it("renders a schedule card with real backend fields", async () => {
    listSchedulesMock.mockResolvedValue([activeSchedule]);
    renderManager();
    await waitFor(() => expect(screen.getByText("daily-sync")).toBeDefined());
    expect(screen.getByText(/wf-1/)).toBeDefined();
  });

  it("creates a schedule with name and definitionId (both required by the real backend)", async () => {
    listSchedulesMock.mockResolvedValue([]);
    createScheduleMock.mockResolvedValue({ ...activeSchedule, id: "sched-new" });
    renderManager();

    await waitFor(() => expect(screen.getByRole("button", { name: "New Schedule" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "New Schedule" }));

    fireEvent.change(await screen.findByLabelText("Schedule Name *"), {
      target: { value: "weekly-report" },
    });
    fireEvent.change(screen.getByLabelText("Workflow Definition ID *"), {
      target: { value: "wf-42" },
    });
    // Default schedule type is INTERVAL; keep its default seconds value.
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createScheduleMock).toHaveBeenCalledWith(
        expect.objectContaining({ name: "weekly-report", definitionId: "wf-42", scheduleType: "INTERVAL" }),
      ),
    );
  });

  it("requires both name and definitionId before submitting", async () => {
    listSchedulesMock.mockResolvedValue([]);
    renderManager();
    await waitFor(() => expect(screen.getByRole("button", { name: "New Schedule" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "New Schedule" }));
    fireEvent.click(await screen.findByRole("button", { name: "Create" }));

    expect(await screen.findByText("Name and Definition ID are required.")).toBeDefined();
    expect(createScheduleMock).not.toHaveBeenCalled();
  });

  it("pauses a schedule using its real id", async () => {
    listSchedulesMock.mockResolvedValue([activeSchedule]);
    pauseScheduleMock.mockResolvedValue(undefined);
    renderManager();

    await waitFor(() => expect(screen.getByRole("button", { name: "Pause schedule daily-sync" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Pause schedule daily-sync" }));

    await waitFor(() => expect(pauseScheduleMock).toHaveBeenCalledWith("sched-1"));
  });

  it("requires confirmation before cancelling a schedule", async () => {
    listSchedulesMock.mockResolvedValue([activeSchedule]);
    cancelScheduleMock.mockResolvedValue(undefined);
    renderManager();

    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel schedule daily-sync" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Cancel schedule daily-sync" }));

    // The destructive action must not fire until confirmed.
    expect(cancelScheduleMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm Cancel" }));

    await waitFor(() => expect(cancelScheduleMock).toHaveBeenCalledWith("sched-1"));
  });

  it("surfaces a mutation failure inline instead of failing silently", async () => {
    listSchedulesMock.mockResolvedValue([activeSchedule]);
    triggerScheduleNowMock.mockRejectedValue(new Error("Scheduler unavailable"));
    renderManager();

    await waitFor(() => expect(screen.getByRole("button", { name: "Trigger schedule daily-sync now" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Trigger schedule daily-sync now" }));

    expect(await screen.findByText("Scheduler unavailable")).toBeDefined();
  });
});
