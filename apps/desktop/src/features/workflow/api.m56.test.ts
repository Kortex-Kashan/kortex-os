/**
 * M5.6 — Workflow Instance, Approval, Schedule, and External Execution API tests.
 *
 * Tests snake_case→camelCase mapping and error dispatch for all new
 * M5.6 IPC capability wrappers. Complements api.test.ts which covers
 * the existing listWorkflowDefinitions.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
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
// Shared fixtures
// ---------------------------------------------------------------------------

const rawInstance = {
  instance_id: "inst-1",
  tenant_id: "acme",
  workflow_id: "wf-1",
  workflow_name: "Demo",
  workflow_version: "1.0",
  status: "RUNNING",
  trigger: "MANUAL",
  priority: "NORMAL",
  current_step_index: 0,
  total_steps: 2,
  steps: [
    {
      step_id: "s1",
      step_name: "Step 1",
      capability_name: "cap.do",
      status: "RUNNING",
      started_at: "2026-01-01T00:00:00Z",
      completed_at: null,
      error_message: null,
      attempt_number: 1,
    },
  ],
  error_message: null,
  started_at: "2026-01-01T00:00:00Z",
  completed_at: null,
  timeout_seconds: 300,
  correlation_id: null,
};

// ---------------------------------------------------------------------------
// WorkflowInstance API
// ---------------------------------------------------------------------------

describe("listWorkflowInstances", () => {
  it("maps snake_case instance list to camelCase WorkflowInstance[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawInstance]));
    const instances = await listWorkflowInstances();
    expect(instances).toHaveLength(1);
    const inst = instances[0];
    expect(inst.instanceId).toBe("inst-1");
    expect(inst.workflowName).toBe("Demo");
    expect(inst.currentStepIndex).toBe(0);
    expect(inst.steps[0].stepId).toBe("s1");
    expect(inst.steps[0].capabilityName).toBe("cap.do");
  });

  it("passes workflowId and status filters as snake_case parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([]));
    await listWorkflowInstances({ workflowId: "wf-1", status: "RUNNING" });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.instance.list",
        parameters: expect.objectContaining({ workflow_id: "wf-1", status: "RUNNING" }),
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
    expect(inst.instanceId).toBe("inst-1");
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
  it("calls instance.start and maps the returned instance", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      ok({ ...rawInstance, instance_id: "inst-new", status: "PENDING" }),
    );
    const inst = await startWorkflowInstance("wf-1", { ctx: true });
    expect(inst.instanceId).toBe("inst-new");
    expect(inst.status).toBe("PENDING");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.workflow.instance.start" }),
    );
  });
});

// ---------------------------------------------------------------------------
// Approval API
// ---------------------------------------------------------------------------

const rawApproval = {
  request_id: "req-1",
  tenant_id: "acme",
  instance_id: "inst-1",
  step_id: "s2",
  workflow_name: "Demo",
  required_role: "admin",
  requester_principal_id: "alice",
  status: "PENDING",
  context: { risk: "low" },
  expires_at: "2026-01-02T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  decided_at: null,
  decider_principal_id: null,
  decision_rationale: null,
};

describe("listPendingApprovals", () => {
  it("maps approval snake_case to camelCase ApprovalRequest[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawApproval]));
    const approvals = await listPendingApprovals();
    expect(approvals).toHaveLength(1);
    const a = approvals[0];
    expect(a.requestId).toBe("req-1");
    expect(a.requiredRole).toBe("admin");
    expect(a.requesterPrincipalId).toBe("alice");
    expect(a.context).toEqual({ risk: "low" });
    expect(a.expiresAt).toBe("2026-01-02T00:00:00Z");
  });

  it("passes status=PENDING filter in parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([]));
    await listPendingApprovals();
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.approval.list",
        parameters: expect.objectContaining({ status: "PENDING" }),
      }),
    );
  });

  it("throws WorkflowAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(listPendingApprovals()).rejects.toBeInstanceOf(WorkflowAccessDeniedError);
  });
});

describe("submitApprovalDecision", () => {
  it("calls approval.decide with snake_case params", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await submitApprovalDecision({ requestId: "req-1", decision: "APPROVED", rationale: "LGTM" });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.approval.decide",
        parameters: expect.objectContaining({
          request_id: "req-1",
          decision: "APPROVED",
          rationale: "LGTM",
        }),
      }),
    );
  });
});

describe("delegateApproval", () => {
  it("calls approval.delegate with snake_case params", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await delegateApproval({ requestId: "req-1", delegateToPrincipalId: "bob", reason: "OOO" });
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.approval.delegate",
        parameters: expect.objectContaining({
          request_id: "req-1",
          delegate_to_principal_id: "bob",
          reason: "OOO",
        }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// Schedule API
// ---------------------------------------------------------------------------

const rawSchedule = {
  schedule_id: "sched-1",
  tenant_id: "acme",
  workflow_id: "wf-1",
  workflow_name: "Demo",
  cron_expression: "0 9 * * *",
  status: "ACTIVE",
  next_run_at: "2026-01-02T09:00:00Z",
  last_run_at: null,
  last_run_status: null,
  run_count: 0,
  max_runs: 10,
  created_at: "2026-01-01T00:00:00Z",
  description: "Daily job",
};

describe("listSchedules", () => {
  it("maps schedule snake_case to camelCase WorkflowSchedule[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawSchedule]));
    const scheds = await listSchedules();
    expect(scheds).toHaveLength(1);
    const s = scheds[0];
    expect(s.scheduleId).toBe("sched-1");
    expect(s.cronExpression).toBe("0 9 * * *");
    expect(s.nextRunAt).toBe("2026-01-02T09:00:00Z");
    expect(s.maxRuns).toBe(10);
    expect(s.description).toBe("Daily job");
  });
});

describe("createSchedule", () => {
  it("calls schedule.create with correct snake_case params and maps result", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      ok({ ...rawSchedule, schedule_id: "sched-2" }),
    );
    const s = await createSchedule({
      workflowId: "wf-1",
      cronExpression: "0 9 * * *",
      description: "Daily job",
      maxRuns: 10,
    });
    expect(s.scheduleId).toBe("sched-2");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.schedule.create",
        parameters: expect.objectContaining({
          workflow_id: "wf-1",
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
  ])("%s calls the %s capability", async (_name, fn, cap) => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    await fn("sched-1");
    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: cap }),
    );
  });
});

// ---------------------------------------------------------------------------
// External Execution API
// ---------------------------------------------------------------------------

const rawExec = {
  execution_id: "exec-1",
  tenant_id: "acme",
  instance_id: "inst-1",
  workflow_id: "wf-1",
  executable: "/usr/bin/python3",
  arguments: ["script.py", "--mode", "safe"],
  working_directory: "/app",
  status: "COMPLETED",
  exit_code: 0,
  stdout: "done\n",
  stderr: null,
  timeout_seconds: 30,
  started_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-01T00:00:05Z",
  approval_request_id: "req-1",
  circuit_breaker_open: false,
};

describe("listExternalExecutions", () => {
  it("maps external execution snake_case to camelCase ExternalExecution[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawExec]));
    const execs = await listExternalExecutions();
    expect(execs).toHaveLength(1);
    const e = execs[0];
    expect(e.executionId).toBe("exec-1");
    expect(e.executable).toBe("/usr/bin/python3");
    expect(e.arguments).toEqual(["script.py", "--mode", "safe"]);
    expect(e.exitCode).toBe(0);
    expect(e.circuitBreakerOpen).toBe(false);
    expect(e.approvalRequestId).toBe("req-1");
    expect(e.completedAt).toBe("2026-01-01T00:00:05Z");
  });

  it("throws WorkflowAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(listExternalExecutions()).rejects.toBeInstanceOf(WorkflowAccessDeniedError);
  });
});
