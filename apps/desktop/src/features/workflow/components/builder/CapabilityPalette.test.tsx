import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { projectCapabilitiesMock } = vi.hoisted(() => ({
  projectCapabilitiesMock: vi.fn(),
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    projectCapabilities: projectCapabilitiesMock,
  };
});

import { CapabilityPalette } from "./CapabilityPalette";

describe("CapabilityPalette", () => {
  beforeEach(() => {
    projectCapabilitiesMock.mockReset();
  });

  it("renders curated capabilities and projected kortex.mcp.* capabilities, rejecting uncurated ones", async () => {
    projectCapabilitiesMock.mockResolvedValueOnce([
      {
        name: "kortex.finance.invoice.get",
        description: "Retrieve invoice details",
        provider: "finance",
        parametersSchema: {},
        isReadOnly: true,
        isIdempotent: true,
      },
      {
        name: "kortex.mcp.github.create_issue",
        description: "Create a GitHub issue via MCP",
        provider: "mcp",
        parametersSchema: {},
        isReadOnly: false,
        isIdempotent: false,
      },
      {
        name: "kortex.untrusted.internal.raw_tool",
        description: "Raw internal tool that should not appear",
        provider: "untrusted",
        parametersSchema: {},
        isReadOnly: false,
        isIdempotent: false,
      },
    ]);

    const onAddCapabilityNode = vi.fn();
    const onAddApprovalNode = vi.fn();

    render(
      <CapabilityPalette
        onAddCapabilityNode={onAddCapabilityNode}
        onAddApprovalNode={onAddApprovalNode}
      />
    );

    // Curated and projected MCP should be present
    expect(await screen.findByText("kortex.finance.invoice.get")).toBeInTheDocument();
    expect(await screen.findByText("kortex.mcp.github.create_issue")).toBeInTheDocument();

    // Raw uncurated tool should NOT be in the palette
    expect(screen.queryByText("kortex.untrusted.internal.raw_tool")).not.toBeInTheDocument();

    // Clicking an MCP capability node calls callback
    const mcpStep = screen.getByTestId("palette-capability-kortex.mcp.github.create_issue");
    fireEvent.click(mcpStep);

    expect(onAddCapabilityNode).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "kortex.mcp.github.create_issue",
      })
    );
  });
});
