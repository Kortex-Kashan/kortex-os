import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import { listWorkflowDefinitions, WorkflowAccessDeniedError, WorkflowRequestError } from "./api";

beforeEach(() => {
  invokeCapabilityMock.mockReset();
});

function successEnvelope(result: unknown) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "SUCCESS" as const,
    payload: { result },
    errors: [],
    warnings: [],
    executionDurationMs: 1,
  };
}

function failureEnvelope(category: string, message: string) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "FAILURE" as const,
    payload: null,
    errors: [{ category, message, correlationId: "corr-1" }],
    warnings: [],
    executionDurationMs: 1,
  };
}

describe("listWorkflowDefinitions", () => {
  it("calls the kortex.workflow.definition.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listWorkflowDefinitions();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.workflow.definition.list",
        parameters: {},
      }),
    );
  });

  it("maps an empty registry to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    const definitions = await listWorkflowDefinitions();

    expect(definitions).toEqual([]);
  });

  it("maps the raw snake_case WorkflowDefinition wire shape into a typed, camelCase WorkflowDefinition", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          id: "wf_demo",
          name: "Demo Workflow",
          version: "1.0.0",
          description: "A demo workflow definition.",
          trigger: "MANUAL",
          priority: "NORMAL",
          timeout_seconds: 3600,
          steps: [
            { id: "s1", name: "Step 1", capability_name: "kortex.connector.action.execute", is_approval_step: false },
          ],
        },
      ]),
    );

    const [definition] = await listWorkflowDefinitions();

    expect(definition).toEqual({
      id: "wf_demo",
      name: "Demo Workflow",
      version: "1.0.0",
      description: "A demo workflow definition.",
      trigger: "MANUAL",
      priority: "NORMAL",
      timeoutSeconds: 3600,
      steps: [
        { id: "s1", name: "Step 1", capabilityName: "kortex.connector.action.execute", isApprovalStep: false },
      ],
    });
  });

  it("never surfaces step parameters or compensation-action data even if present on the wire", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          id: "wf_demo",
          name: "Demo Workflow",
          version: "1.0.0",
          description: "A demo workflow definition.",
          trigger: "MANUAL",
          priority: "NORMAL",
          timeout_seconds: 3600,
          steps: [
            {
              id: "s1",
              name: "Step 1",
              capability_name: "kortex.connector.action.execute",
              is_approval_step: false,
              // Hypothetical author-supplied step data that could carry
              // sensitive values — `toWorkflowDefinition` only reads
              // known-safe fields by name, so this is silently dropped.
              parameters: { apiKey: "should_never_appear" },
              compensation_action: { name: "rollback", parameters: { secret: "should_never_appear" } },
            },
          ],
        },
      ]),
    );

    const [definition] = await listWorkflowDefinitions();

    expect(JSON.stringify(definition)).not.toMatch(/should_never_appear/i);
  });

  it("throws WorkflowAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listWorkflowDefinitions()).rejects.toBeInstanceOf(WorkflowAccessDeniedError);
  });

  it("throws WorkflowRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("SERVICE_UNAVAILABLE", "backend unreachable"));

    await expect(listWorkflowDefinitions()).rejects.toBeInstanceOf(WorkflowRequestError);
  });
});
