import * as React from "react";
import { Badge, Button, Input } from "@kortex/design-system";
import type {
  WorkflowExpressionValue,
  WorkflowLiteralValue,
  WorkflowOperator,
  WorkflowReferenceValue,
  WorkflowValue,
} from "../../types";

export interface AncestorOption {
  nodeId: string;
  name: string;
  outputFields?: string[];
}

export interface MappingEditorProps {
  fieldName: string;
  fieldSchema?: Record<string, unknown>;
  value: WorkflowValue | undefined;
  ancestors: AncestorOption[];
  onChange: (newValue: WorkflowValue) => void;
  onClear: () => void;
}

const OPERATORS: WorkflowOperator[] = ["CONCAT", "SUM", "SUBTRACT", "LENGTH"];

export function MappingEditor({
  fieldName,
  fieldSchema: _fieldSchema,
  value,
  ancestors,
  onChange,
  onClear,
}: MappingEditorProps): React.JSX.Element {
  const currentKind = value?.kind ?? "literal";

  const handleKindChange = (kind: "literal" | "reference" | "expression") => {
    if (kind === "literal") {
      onChange({ kind: "literal", value: "" });
    } else if (kind === "reference") {
      const defaultSource = ancestors.length > 0 ? ancestors[0].nodeId : "";
      onChange({
        kind: "reference",
        reference: {
          sourceNodeId: defaultSource,
          sourcePort: null,
          path: [],
        },
      });
    } else if (kind === "expression") {
      onChange({
        kind: "expression",
        expression: {
          operator: "CONCAT",
          operands: [
            { kind: "literal", value: "" },
            { kind: "literal", value: "" },
          ],
        },
      });
    }
  };

  return (
    <div
      className="p-3 rounded-md border border-border bg-card/40 space-y-3"
      data-testid={`mapping-editor-${fieldName}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-medium text-foreground">{fieldName}</span>
          <Badge variant="outline" className="text-[10px] px-1 py-0 uppercase">
            F3 Mapping
          </Badge>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClear}
          className="h-6 text-[10px] text-muted-foreground hover:text-destructive px-1.5"
          data-testid={`mapping-clear-${fieldName}`}
        >
          Clear
        </Button>
      </div>

      {/* Kind selector: Literal | Reference | Expression */}
      <div className="flex rounded-md bg-muted/60 p-0.5 border border-border">
        {(["literal", "reference", "expression"] as const).map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => handleKindChange(kind)}
            className={`flex-1 text-[11px] py-1 font-medium capitalize rounded transition-colors ${
              currentKind === kind
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
            data-testid={`mapping-kind-${kind}`}
          >
            {kind}
          </button>
        ))}
      </div>

      {/* Literal Editor */}
      {currentKind === "literal" && (
        <div className="space-y-1.5">
          <label className="text-[11px] text-muted-foreground">Literal Value</label>
          <Input
            value={String((value as WorkflowLiteralValue)?.value ?? "")}
            onChange={(e) => onChange({ kind: "literal", value: e.target.value })}
            placeholder="Constant value..."
            className="h-8 text-xs font-mono"
            data-testid={`mapping-literal-input-${fieldName}`}
          />
        </div>
      )}

      {/* Reference Editor */}
      {currentKind === "reference" && (
        <div className="space-y-2">
          {ancestors.length === 0 ? (
            <div className="p-2 text-xs text-amber-500/90 border border-amber-500/20 rounded bg-amber-500/5">
              No upstream ancestor steps available to reference. Only outputs from prior steps can be referenced.
            </div>
          ) : (
            <>
              <div className="space-y-1">
                <label className="text-[11px] text-muted-foreground">Source Step (Ancestor)</label>
                <select
                  value={(value as WorkflowReferenceValue)?.reference?.sourceNodeId ?? ""}
                  onChange={(e) => {
                    const currentRef = (value as WorkflowReferenceValue)?.reference;
                    onChange({
                      kind: "reference",
                      reference: {
                        sourceNodeId: e.target.value,
                        sourcePort: currentRef?.sourcePort ?? null,
                        path: currentRef?.path ?? [],
                      },
                    });
                  }}
                  className="w-full h-8 text-xs rounded-md border border-input bg-background px-2 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  data-testid={`mapping-reference-source-${fieldName}`}
                >
                  {ancestors.map((anc) => (
                    <option key={anc.nodeId} value={anc.nodeId}>
                      {anc.name} ({anc.nodeId})
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] text-muted-foreground">Output Field / Dotted Path</label>
                <Input
                  value={((value as WorkflowReferenceValue)?.reference?.path ?? []).join(".")}
                  onChange={(e) => {
                    const raw = e.target.value.trim();
                    const pathSegments = raw.length > 0 ? raw.split(".") : [];
                    const currentRef = (value as WorkflowReferenceValue)?.reference;
                    onChange({
                      kind: "reference",
                      reference: {
                        sourceNodeId: currentRef?.sourceNodeId ?? (ancestors[0]?.nodeId || ""),
                        sourcePort: currentRef?.sourcePort ?? null,
                        path: pathSegments,
                      },
                    });
                  }}
                  placeholder="e.g. invoice_id or customer_name"
                  className="h-8 text-xs font-mono"
                  data-testid={`mapping-reference-path-${fieldName}`}
                />
              </div>
            </>
          )}
        </div>
      )}

      {/* Expression Editor */}
      {currentKind === "expression" && (
        <div className="space-y-2.5">
          <div className="space-y-1">
            <label className="text-[11px] text-muted-foreground">Operator</label>
            <select
              value={(value as WorkflowExpressionValue)?.expression?.operator ?? "CONCAT"}
              onChange={(e) => {
                const expr = (value as WorkflowExpressionValue)?.expression;
                onChange({
                  kind: "expression",
                  expression: {
                    operator: e.target.value as WorkflowOperator,
                    operands: expr?.operands ?? [],
                  },
                });
              }}
              className="w-full h-8 text-xs rounded-md border border-input bg-background px-2 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              data-testid={`mapping-expression-operator-${fieldName}`}
            >
              {OPERATORS.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-[11px] text-muted-foreground">Operands</label>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const expr = (value as WorkflowExpressionValue)?.expression;
                  const currentOperands = expr?.operands ?? [];
                  onChange({
                    kind: "expression",
                    expression: {
                      operator: expr?.operator ?? "CONCAT",
                      operands: [...currentOperands, { kind: "literal", value: "" }],
                    },
                  });
                }}
                className="h-6 text-[10px] px-2"
                data-testid={`mapping-add-operand-${fieldName}`}
              >
                + Add Operand
              </Button>
            </div>

            {((value as WorkflowExpressionValue)?.expression?.operands ?? []).map((op, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 p-1.5 rounded bg-muted/30 border border-border"
              >
                <span className="text-[10px] text-muted-foreground w-4 text-center">{idx + 1}.</span>
                <select
                  value={op.kind}
                  onChange={(e) => {
                    const newKind = e.target.value as "literal" | "reference";
                    const expr = (value as WorkflowExpressionValue).expression;
                    const newOperands = [...expr.operands];
                    if (newKind === "literal") {
                      newOperands[idx] = { kind: "literal", value: "" };
                    } else {
                      newOperands[idx] = {
                        kind: "reference",
                        reference: {
                          sourceNodeId: ancestors[0]?.nodeId || "",
                          sourcePort: null,
                          path: [],
                        },
                      };
                    }
                    onChange({
                      kind: "expression",
                      expression: { ...expr, operands: newOperands },
                    });
                  }}
                  className="h-7 text-[11px] rounded border border-input bg-background px-1"
                >
                  <option value="literal">Literal</option>
                  <option value="reference">Reference</option>
                </select>

                {op.kind === "literal" ? (
                  <Input
                    value={String(op.value ?? "")}
                    onChange={(e) => {
                      const expr = (value as WorkflowExpressionValue).expression;
                      const newOperands = [...expr.operands];
                      newOperands[idx] = { kind: "literal", value: e.target.value };
                      onChange({
                        kind: "expression",
                        expression: { ...expr, operands: newOperands },
                      });
                    }}
                    placeholder="Operand value..."
                    className="h-7 text-xs font-mono flex-1"
                  />
                ) : (
                  (() => {
                    const refVal = op as WorkflowReferenceValue;
                    return (
                      <div className="flex gap-1 flex-1">
                        <select
                          value={refVal.reference?.sourceNodeId ?? ""}
                          onChange={(e) => {
                            const expr = (value as WorkflowExpressionValue).expression;
                            const newOperands = [...expr.operands];
                            newOperands[idx] = {
                              kind: "reference",
                              reference: {
                                sourceNodeId: e.target.value,
                                sourcePort: null,
                                path: refVal.reference?.path ?? [],
                              },
                            };
                            onChange({
                              kind: "expression",
                              expression: { ...expr, operands: newOperands },
                            });
                          }}
                          className="h-7 text-[11px] rounded border border-input bg-background px-1 flex-1"
                        >
                          {ancestors.map((anc) => (
                            <option key={anc.nodeId} value={anc.nodeId}>
                              {anc.name}
                            </option>
                          ))}
                        </select>
                        <Input
                          value={(refVal.reference?.path ?? []).join(".")}
                          onChange={(e) => {
                            const raw = e.target.value.trim();
                            const expr = (value as WorkflowExpressionValue).expression;
                            const newOperands = [...expr.operands];
                            newOperands[idx] = {
                              kind: "reference",
                              reference: {
                                sourceNodeId: refVal.reference?.sourceNodeId || ancestors[0]?.nodeId || "",
                                sourcePort: null,
                                path: raw.length > 0 ? raw.split(".") : [],
                              },
                            };
                            onChange({
                              kind: "expression",
                              expression: { ...expr, operands: newOperands },
                            });
                          }}
                          placeholder="field"
                          className="h-7 text-xs font-mono w-24"
                        />
                      </div>
                    );
                  })()
                )}

                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    const expr = (value as WorkflowExpressionValue).expression;
                    const newOperands = expr.operands.filter((_, i) => i !== idx);
                    onChange({
                      kind: "expression",
                      expression: { ...expr, operands: newOperands },
                    });
                  }}
                  className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                >
                  ✕
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
