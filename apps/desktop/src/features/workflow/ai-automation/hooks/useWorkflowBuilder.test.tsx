import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  generateMock,
  correctMock,
  createDraftMock,
  updateDraftMock,
  validateDraftMock,
  publishDraftMock,
} = vi.hoisted(() => ({
  generateMock: vi.fn(),
  correctMock: vi.fn(),
  createDraftMock: vi.fn(),
  updateDraftMock: vi.fn(),
  validateDraftMock: vi.fn(),
  publishDraftMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    generateWorkflowProposal: generateMock,
    correctWorkflowProposal: correctMock,
    createWorkflowDraft: createDraftMock,
    updateWorkflowDraft: updateDraftMock,
    validateWorkflowDraft: validateDraftMock,
    publishWorkflowDraft: publishDraftMock,
  };
});

import { useWorkflowBuilder } from "./useWorkflowBuilder";

const VALID_PROPOSAL = {
  status: "proposed" as const,
  errors: [],
  warnings: [],
  graph: { entryNodeId: "step_1", nodes: [], edges: [], raw: { entry_node_id: "step_1", nodes: [], edges: [] } },
  name: "Test workflow",
  description: "d",
  conversationId: "conv-1",
};

beforeEach(() => {
  generateMock.mockReset();
  correctMock.mockReset();
  createDraftMock.mockReset();
  updateDraftMock.mockReset();
  validateDraftMock.mockReset();
  publishDraftMock.mockReset();
});

describe("useWorkflowBuilder", () => {
  it("never persists anything from generate() alone -- create/update/publish are never called", async () => {
    generateMock.mockResolvedValueOnce(VALID_PROPOSAL);
    const { result } = renderHook(() => useWorkflowBuilder());

    await act(async () => {
      await result.current.generate("do something");
    });

    expect(result.current.phase).toBe("proposed");
    expect(createDraftMock).not.toHaveBeenCalled();
    expect(updateDraftMock).not.toHaveBeenCalled();
    expect(publishDraftMock).not.toHaveBeenCalled();
  });

  it("creates a draft and validates only after the explicit createDraft() call", async () => {
    generateMock.mockResolvedValueOnce(VALID_PROPOSAL);
    createDraftMock.mockResolvedValueOnce({ definitionId: "def-1", lockVersion: 0, status: "DRAFT" });
    validateDraftMock.mockResolvedValueOnce({ isValid: true, errors: [], warnings: [] });

    const { result } = renderHook(() => useWorkflowBuilder());
    await act(async () => {
      await result.current.generate("do something");
    });
    expect(createDraftMock).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.createDraft();
    });

    expect(createDraftMock).toHaveBeenCalledTimes(1);
    expect(validateDraftMock).toHaveBeenCalledWith("def-1");
    expect(result.current.phase).toBe("ready_for_review");
    expect(publishDraftMock).not.toHaveBeenCalled();
  });

  it("never publishes except from the explicit publish() action", async () => {
    generateMock.mockResolvedValueOnce(VALID_PROPOSAL);
    createDraftMock.mockResolvedValueOnce({ definitionId: "def-1", lockVersion: 0, status: "DRAFT" });
    validateDraftMock.mockResolvedValueOnce({ isValid: true, errors: [], warnings: [] });
    publishDraftMock.mockResolvedValueOnce({ definitionId: "def-1", lockVersion: 1, status: "PUBLISHED" });

    const { result } = renderHook(() => useWorkflowBuilder());
    await act(async () => {
      await result.current.generate("do something");
    });
    await act(async () => {
      await result.current.createDraft();
    });
    expect(publishDraftMock).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.publish();
    });

    expect(publishDraftMock).toHaveBeenCalledTimes(1);
    expect(result.current.phase).toBe("published");
  });

  it("bounds the correction loop at MAX_CORRECTION_ATTEMPTS (3)", async () => {
    generateMock.mockResolvedValueOnce(VALID_PROPOSAL);
    createDraftMock.mockResolvedValueOnce({ definitionId: "def-1", lockVersion: 0, status: "DRAFT" });
    validateDraftMock.mockResolvedValueOnce({ isValid: false, errors: ["bad"], warnings: [] });
    correctMock.mockResolvedValue(VALID_PROPOSAL);
    updateDraftMock.mockResolvedValue({ definitionId: "def-1", lockVersion: 1, status: "DRAFT" });
    validateDraftMock.mockResolvedValue({ isValid: false, errors: ["still bad"], warnings: [] });

    const { result } = renderHook(() => useWorkflowBuilder());
    await act(async () => {
      await result.current.generate("do something");
    });
    await act(async () => {
      await result.current.createDraft();
    });
    expect(result.current.phase).toBe("correcting");

    await act(async () => {
      await result.current.correct();
    });
    await act(async () => {
      await result.current.correct();
    });
    await act(async () => {
      await result.current.correct();
    });

    expect(correctMock).toHaveBeenCalledTimes(3);
    expect(result.current.phase).toBe("correction_exhausted");
    expect(publishDraftMock).not.toHaveBeenCalled();

    // A 4th call must be a no-op -- the bound is enforced, not merely advisory.
    await act(async () => {
      await result.current.correct();
    });
    expect(correctMock).toHaveBeenCalledTimes(3);
  });

  it("surfaces generation_failed without ever touching F4 capabilities", async () => {
    generateMock.mockResolvedValueOnce({
      status: "generation_failed",
      errors: ["capability not in curated set"],
      warnings: [],
      graph: null,
      name: "",
      description: "",
      conversationId: "conv-1",
    });

    const { result } = renderHook(() => useWorkflowBuilder());
    await act(async () => {
      await result.current.generate("do something bad");
    });

    expect(result.current.phase).toBe("generation_failed");
    expect(createDraftMock).not.toHaveBeenCalled();
  });
});
