import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { BuilderFlowPhase, WorkflowBuilderProposal, WorkflowDraftHandle } from "../types";

const { useWorkflowBuilderMock } = vi.hoisted(() => ({ useWorkflowBuilderMock: vi.fn() }));

vi.mock("../hooks/useWorkflowBuilder", () => ({ useWorkflowBuilder: useWorkflowBuilderMock }));

import { WorkflowBuilderPanel } from "./WorkflowBuilderPanel";

const BASE_STATE = {
  proposal: null as WorkflowBuilderProposal | null,
  draft: null as WorkflowDraftHandle | null,
  validationErrors: [] as string[],
  validationWarnings: [] as string[],
  correctionAttempt: 0,
  maxCorrectionAttempts: 3,
  error: null as string | null,
  generate: vi.fn(),
  createDraft: vi.fn(),
  correct: vi.fn(),
  publish: vi.fn(),
  reset: vi.fn(),
};

function mockPhase(phase: BuilderFlowPhase, overrides: Partial<typeof BASE_STATE> = {}) {
  useWorkflowBuilderMock.mockReturnValue({ ...BASE_STATE, phase, ...overrides });
}

describe("WorkflowBuilderPanel", () => {
  it("shows only the Generate action in the idle phase", () => {
    mockPhase("idle");
    render(<WorkflowBuilderPanel />);

    expect(screen.getByTestId("builder-generate-button")).toBeInTheDocument();
    expect(screen.queryByTestId("builder-create-draft-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("builder-approve-publish-button")).not.toBeInTheDocument();
  });

  it("shows only the Create Draft action once a proposal exists, never Approve & Publish", () => {
    mockPhase("proposed", {
      proposal: {
        status: "proposed",
        errors: [],
        warnings: [],
        graph: {
          entryNodeId: "step_1",
          nodes: [{ nodeId: "step_1", capabilityName: "kortex.finance.invoice.get", config: {}, isApprovalStep: false }],
          edges: [],
          raw: {},
        },
        name: "Notify about invoice",
        description: "",
        conversationId: "conv-1",
      },
    });
    render(<WorkflowBuilderPanel />);

    expect(screen.getByTestId("builder-create-draft-button")).toBeInTheDocument();
    expect(screen.queryByTestId("builder-generate-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("builder-approve-publish-button")).not.toBeInTheDocument();
    expect(screen.getByTestId("builder-node-row")).toBeInTheDocument();
  });

  it("shows only the Approve & Publish action once the draft is ready for review, never Create Draft", () => {
    mockPhase("ready_for_review", {
      draft: { definitionId: "def-1", lockVersion: 0, status: "DRAFT" },
    });
    render(<WorkflowBuilderPanel />);

    expect(screen.getByTestId("builder-approve-publish-button")).toBeInTheDocument();
    expect(screen.queryByTestId("builder-create-draft-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("builder-generate-button")).not.toBeInTheDocument();
  });

  it("shows the published success state with no further action buttons except 'build another'", () => {
    mockPhase("published", {
      draft: { definitionId: "def-1", lockVersion: 1, status: "PUBLISHED" },
    });
    render(<WorkflowBuilderPanel />);

    expect(screen.getByTestId("builder-published")).toBeInTheDocument();
    expect(screen.queryByTestId("builder-approve-publish-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("builder-create-draft-button")).not.toBeInTheDocument();
  });

  it("shows the bounded correction state and count", () => {
    mockPhase("correcting", {
      validationErrors: ["Node 'x' references unknown capability 'y'."],
      correctionAttempt: 1,
    });
    render(<WorkflowBuilderPanel />);

    expect(screen.getByTestId("builder-correction-state")).toHaveTextContent("1 of 3");
    expect(screen.getByTestId("builder-correct-button")).toBeInTheDocument();
  });
});
