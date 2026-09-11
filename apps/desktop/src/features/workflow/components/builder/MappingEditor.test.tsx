import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowExpressionValue, WorkflowLiteralValue, WorkflowReferenceValue } from "../../types";
import { type AncestorOption, MappingEditor } from "./MappingEditor";

describe("MappingEditor", () => {
  const ancestors: AncestorOption[] = [
    { nodeId: "step_1", name: "Fetch Invoice", outputFields: ["invoice_id", "total_amount"] },
    { nodeId: "step_2", name: "Validate Invoice", outputFields: ["is_valid"] },
  ];

  it("renders literal value by default and handles edit", () => {
    const onChange = vi.fn();
    const onClear = vi.fn();

    const literalVal: WorkflowLiteralValue = { kind: "literal", value: "hello" };

    render(
      <MappingEditor
        fieldName="greeting"
        value={literalVal}
        ancestors={ancestors}
        onChange={onChange}
        onClear={onClear}
      />,
    );

    const input = screen.getByTestId("mapping-literal-input-greeting") as HTMLInputElement;
    expect(input.value).toBe("hello");

    fireEvent.change(input, { target: { value: "world" } });
    expect(onChange).toHaveBeenCalledWith({ kind: "literal", value: "world" });
  });

  it("switches to reference mapping and configures ancestor and path", () => {
    const onChange = vi.fn();
    const onClear = vi.fn();

    render(
      <MappingEditor
        fieldName="target_id"
        value={{ kind: "literal", value: "" }}
        ancestors={ancestors}
        onChange={onChange}
        onClear={onClear}
      />,
    );

    // Switch to reference
    const refKindBtn = screen.getByTestId("mapping-kind-reference");
    fireEvent.click(refKindBtn);

    expect(onChange).toHaveBeenCalledWith({
      kind: "reference",
      reference: {
        sourceNodeId: "step_1",
        sourcePort: null,
        path: [],
      },
    });

    // Render with reference value
    const refVal: WorkflowReferenceValue = {
      kind: "reference",
      reference: {
        sourceNodeId: "step_1",
        sourcePort: null,
        path: ["invoice_id"],
      },
    };

    render(
      <MappingEditor
        fieldName="target_id"
        value={refVal}
        ancestors={ancestors}
        onChange={onChange}
        onClear={onClear}
      />,
    );

    const pathInput = screen.getByTestId("mapping-reference-path-target_id") as HTMLInputElement;
    expect(pathInput.value).toBe("invoice_id");

    fireEvent.change(pathInput, { target: { value: "total_amount" } });
    expect(onChange).toHaveBeenCalledWith({
      kind: "reference",
      reference: {
        sourceNodeId: "step_1",
        sourcePort: null,
        path: ["total_amount"],
      },
    });
  });

  it("displays warning when reference mode has no available ancestors", () => {
    const onChange = vi.fn();
    const onClear = vi.fn();

    const refVal: WorkflowReferenceValue = {
      kind: "reference",
      reference: {
        sourceNodeId: "",
        sourcePort: null,
        path: [],
      },
    };

    render(
      <MappingEditor
        fieldName="input_data"
        value={refVal}
        ancestors={[]}
        onChange={onChange}
        onClear={onClear}
      />,
    );

    expect(screen.getByText(/No upstream ancestor steps available to reference/i)).toBeInTheDocument();
  });

  it("switches to expression mapping and changes operator", () => {
    const onChange = vi.fn();
    const onClear = vi.fn();

    const exprVal: WorkflowExpressionValue = {
      kind: "expression",
      expression: {
        operator: "CONCAT",
        operands: [
          { kind: "literal", value: "prefix-" },
          { kind: "reference", reference: { sourceNodeId: "step_1", path: ["invoice_id"] } },
        ],
      },
    };

    render(
      <MappingEditor
        fieldName="order_code"
        value={exprVal}
        ancestors={ancestors}
        onChange={onChange}
        onClear={onClear}
      />,
    );

    const operatorSelect = screen.getByTestId("mapping-expression-operator-order_code") as HTMLSelectElement;
    expect(operatorSelect.value).toBe("CONCAT");

    fireEvent.change(operatorSelect, { target: { value: "SUM" } });
    expect(onChange).toHaveBeenCalledWith({
      kind: "expression",
      expression: {
        operator: "SUM",
        operands: exprVal.expression.operands,
      },
    });
  });

  it("calls onClear when clear button is clicked", () => {
    const onChange = vi.fn();
    const onClear = vi.fn();

    render(
      <MappingEditor
        fieldName="amount"
        value={{ kind: "literal", value: 123 }}
        ancestors={ancestors}
        onChange={onChange}
        onClear={onClear}
      />,
    );

    const clearBtn = screen.getByTestId("mapping-clear-amount");
    fireEvent.click(clearBtn);

    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
