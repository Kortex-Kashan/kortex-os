/**
 * M5.6 — Workflow Instance, Approval, Schedule, and External Execution API tests.
 *
 * Hardened M5-A6: every fixture below is the ACTUAL backend response shape
 * (the real `WorkflowInstance` model fields, or the exact hand-built dict
 * each approval/schedule/external-execution capability handler returns —
 * verified against `backend/src/kortex/engines/workflow/engine.py`), not
 * an invented shape the frontend happened to expect. The pre-M5-A6 version
 * of this file's fixtures were authored to match the frontend's own wrong
 * `Raw*` interfaces, which is exactly why the mismatch shipped undetected —
 * this file's tests passed while asserting a contract that did not exist in
 * production.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  cancelExternalExecution,
  cancelSchedule,
  cancelWorkflowInstance,
  createSchedule,
  delegateApproval,
  getWorkflowInstance,
  listExternalExecutions,
  listPendingApprovals,
  listSchedules,
  listWorkflowInstances,
  pauseSchedule,
  resumeSchedule,
  startWorkflowInstance,
  submitApprovalDecision,
  triggerScheduleNow,
  WorkflowAccessDeniedError,
} from "./api";

function ok(result: unknown) {
  return {
    requestId: "r1",
    correlationId: "c1",
    status: "SUCCESS" as const,
    payload: { result },
    errors: [],
    warnings: [],
    executionDurationMs: 1,
  };
}

function fail(category: string, message: string) {
  return {
    requestId: "r1",
    correlationId: "c1",
    status: "FAILURE" as const,
    payload: null,
    errors: [{ category, message, correlationId: "c1" }],
    warnings: [],
    executionDurationMs: 1,
  };
}

beforeEach(() => {
  invokeCapabilityMock.mockReset();
});

// ---------------------------------------------------------------------------
// Shared fixtures — the real WorkflowInstance model, verbatim
// ---------------------------------------------------------------------------

const rawInstance = {
  id: "inst-1",
  definition_id: "wf-1",
  definition_version: "1.0.0",
  tenant_id: "acme",
  current_step_index: 0,
  current_step_id: "step_a",
  state: "RUNNING",
  status: "RUNNING",
  trace_id: "trace-1",
  version: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

// ---------------------------------------------------------------------------
// WorkflowInstance API
// ---------------------------------------------------------------------------

describe("listWorkflowInstances", () => {
  it("maps the real WorkflowInstance model to camelCase", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawInstance]));
    const instances = await listWorkflowInstances();
    expect(instances).toHaveLength(1);
    const inst = instances[0];
    expect(inst.id).toBe("inst-1");
    expect(inst.definitionId).toBe("wf-1");
    expect(inst.currentStepIndex).toBe(0);
    expect(inst.currentStepId).toBe("step_a");
    expect(inst.state).toBe("RUNNING");
    expect(inst.status).toBe("RUNNING");
    expect(inst.traceId).toBe("trace-1");
  });

  it("passes the state filter as the only supported snake_case parameter", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([]));
    await listWorkflowInstances({ state: "RUNNING" });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.instance.list",
        parameters: expect.objectContaining({ state: "RUNNING" }),
      }),
    );
  });

  it("throws WorkflowAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(listWorkflowInstances()).rejects.toBeInstanceOf(WorkflowAccessDeniedError);
  });
});

describe("getWorkflowInstance", () => {
  it("calls instance.get with instance_id and maps result", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok({ ...rawInstance, status: "COMPLETED" }));
    const inst = await getWorkflowInstance("inst-1");
    expect(inst.id).toBe("inst-1");
    expect(inst.status).toBe("COMPLETED");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.instance.get",
        parameters: expect.objectContaining({ instance_id: "inst-1" }),
      }),
    );
  });
});

describe("cancelWorkflowInstance", () => {
  it("calls instance.cancel with instance_id and reason", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await cancelWorkflowInstance("inst-1", "operator cancel");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.instance.cancel",
        parameters: expect.objectContaining({ instance_id: "inst-1", reason: "operator cancel" }),
      }),
    );
  });
});

describe("startWorkflowInstance", () => {
  it("calls instance.start with definition_id/initial_context and maps the returned instance", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok({ ...rawInstance, id: "inst-new", status: "PENDING" }));
    const inst = await startWorkflowInstance("wf-1", { ctx: true });
    expect(inst.id).toBe("inst-new");
    expect(inst.status).toBe("PENDING");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.instance.start",
        parameters: expect.objectContaining({ definition_id: "wf-1", initial_context: { ctx: true } }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// Approval API — exact hand-built dicts from create_approval_request /
// list_approval_requests / get_approval_request / decide_approval_request
// ---------------------------------------------------------------------------

const rawApproval = {
  id: "req-1",
  tenant_id: "acme",
  instance_id: "inst-1",
  step_id: "s2",
  required_role: "admin",
  state: "PENDING",
  timeout_at: "2026-01-02T00:00:00Z",
  signature_required: false,
};

describe("listPendingApprovals", () => {
  it("maps the real approval dict shape to camelCase ApprovalRequest[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawApproval]));
    const approvals = await listPendingApprovals();
    expect(approvals).toHaveLength(1);
    const a = approvals[0];
    expect(a.id).toBe("req-1");
    expect(a.requiredRole).toBe("admin");
    expect(a.state).toBe("PENDING");
    expect(a.timeoutAt).toBe("2026-01-02T00:00:00Z");
    expect(a.signatureRequired).toBe(false);
  });

  it("passes state_filter=PENDING (the real backend parameter name)", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([]));
    await listPendingApprovals();
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.approval.list",
        parameters: expect.objectContaining({ state_filter: "PENDING" }),
      }),
    );
  });

  it("throws WorkflowAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(listPendingApprovals()).rejects.toBeInstanceOf(WorkflowAccessDeniedError);
  });
});

describe("submitApprovalDecision", () => {
  it("calls approval.decide with approver_id/reason (the real backend parameter names)", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await submitApprovalDecision({
      requestId: "req-1",
      decision: "APPROVED",
      approverId: "alice",
      reason: "LGTM",
    });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.approval.decide",
        parameters: expect.objectContaining({
          request_id: "req-1",
          decision: "APPROVED",
          approver_id: "alice",
          reason: "LGTM",
        }),
      }),
    );
  });
});

describe("delegateApproval", () => {
  it("calls approval.delegate with delegator_id/delegatee_id/role/valid_from/valid_until", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await delegateApproval({
      delegatorId: "alice",
      delegateeId: "bob",
      role: "admin",
      validFrom: "2026-01-01T00:00:00Z",
      validUntil: "2026-01-02T00:00:00Z",
    });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.approval.delegate",
        parameters: expect.objectContaining({
          delegator_id: "alice",
          delegatee_id: "bob",
          role: "admin",
          valid_from: "2026-01-01T00:00:00Z",
          valid_until: "2026-01-02T00:00:00Z",
        }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// Schedule API — exact hand-built dicts from create_schedule / list_schedules
// / get_schedule / pause / resume / cancel
// ---------------------------------------------------------------------------

const rawSchedule = {
  id: "sched-1",
  name: "daily-sync",
  definition_id: "wf-1",
  schedule_type: "CRON",
  cron_expression: "0 9 * * *",
  interval_seconds: null,
  next_run_at: "2026-01-02T09:00:00Z",
  status: "ACTIVE",
  run_count: 0,
  tenant_id: "acme",
};

describe("listSchedules", () => {
  it("maps the real schedule dict shape to camelCase WorkflowSchedule[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawSchedule]));
    const scheds = await listSchedules();
    expect(scheds).toHaveLength(1);
    const s = scheds[0];
    expect(s.id).toBe("sched-1");
    expect(s.name).toBe("daily-sync");
    expect(s.definitionId).toBe("wf-1");
    expect(s.cronExpression).toBe("0 9 * * *");
    expect(s.nextRunAt).toBe("2026-01-02T09:00:00Z");
  });
});

describe("createSchedule", () => {
  it("calls schedule.create with name/definition_id (both required by the real backend) and maps result", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok({ ...rawSchedule, id: "sched-2" }));
    const s = await createSchedule({
      name: "daily-sync",
      definitionId: "wf-1",
      scheduleType: "CRON",
      cronExpression: "0 9 * * *",
      maxRuns: 10,
    });
    expect(s.id).toBe("sched-2");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.schedule.create",
        parameters: expect.objectContaining({
          name: "daily-sync",
          definition_id: "wf-1",
          schedule_type: "CRON",
          cron_expression: "0 9 * * *",
          max_runs: 10,
        }),
      }),
    );
  });
});

describe("schedule mutation operations", () => {
  it.each([
    ["pauseSchedule", pauseSchedule, "kortex.workflow.schedule.pause"],
    ["resumeSchedule", resumeSchedule, "kortex.workflow.schedule.resume"],
    ["cancelSchedule", cancelSchedule, "kortex.workflow.schedule.cancel"],
    ["triggerScheduleNow", triggerScheduleNow, "kortex.workflow.schedule.trigger"],
  ])("%s calls the %s capability with schedule_id", async (_name, fn, cap) => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await fn("sched-1");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: cap,
        parameters: expect.objectContaining({ schedule_id: "sched-1" }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// External Execution API — exact hand-built dicts from execute_external_operation
// / get_external_execution / list_external_executions / cancel_external_execution
// ---------------------------------------------------------------------------

const rawExec = {
  id: "exec-1",
  status: "COMPLETED",
  target: "kortex.some.capability",
  output: { ok: true },
  error: null,
  attempts: 1,
  execution_time_ms: 42.5,
  approval_request_id: "req-1",
  tenant_id: "acme",
};

describe("listExternalExecutions", () => {
  it("maps the real external-execution dict shape to camelCase", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawExec]));
    const execs = await listExternalExecutions();
    expect(execs).toHaveLength(1);
    const e = execs[0];
    expect(e.id).toBe("exec-1");
    expect(e.target).toBe("kortex.some.capability");
    expect(e.output).toEqual({ ok: true });
    expect(e.attempts).toBe(1);
    expect(e.executionTimeMs).toBe(42.5);
    expect(e.approvalRequestId).toBe("req-1");
  });

  it("throws WorkflowAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(listExternalExecutions()).rejects.toBeInstanceOf(WorkflowAccessDeniedError);
  });
});

describe("cancelExternalExecution", () => {
  it("calls external.cancel with execution_id", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await cancelExternalExecution("exec-1");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.external.cancel",
        parameters: expect.objectContaining({ execution_id: "exec-1" }),
      }),
    );
  });
});
