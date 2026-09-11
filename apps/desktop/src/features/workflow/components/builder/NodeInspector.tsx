import * as React from "react";
import { Badge, Button, Input } from "@kortex/design-system";
import type {
  ProjectedCapability,
  WorkflowGraphNode,
  WorkflowMapping,
  WorkflowValue,
} from "../../types";
import { MappingEditor, type AncestorOption } from "./MappingEditor";

export interface NodeInspectorProps {
  node: WorkflowGraphNode | null;
  capability: ProjectedCapability | null;
  ancestors: AncestorOption[];
  onUpdateNode: (updatedNode: WorkflowGraphNode) => void;
  onDeleteNode: (nodeId: string) => void;
  onClose: () => void;
}

const STEP_METADATA_KEY = "kortex.workflow.step";

export function NodeInspector({
  node,
  capability,
  ancestors,
  onUpdateNode,
  onDeleteNode,
  onClose,
}: NodeInspectorProps): React.JSX.Element {
  if (!node) {
    return (
      <aside
        aria-label="Node Inspector"
        className="w-80 border-l border-border bg-card/40 backdrop-blur p-4 flex flex-col items-center justify-center text-center text-muted-foreground select-none"
        data-testid="node-inspector-empty"
      >
        <svg
          className="w-10 h-10 text-muted-foreground/40 mb-2"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
          />
        </svg>
        <p className="text-xs font-medium text-foreground">No step selected</p>
        <p className="text-[11px] mt-1">
          Click any step on the canvas to configure its parameters, mappings, or approval rules.
        </p>
      </aside>
    );
  }

  const isApprovalNode = node.nodeType === "approval";
  const stepMetadata = ((node.metadata?.[STEP_METADATA_KEY] ?? {}) as Record<string, unknown>);
  const stepName = String(stepMetadata.name ?? node.nodeId);
  const requiredRole = String(stepMetadata.required_approval_role ?? "");

  // Parameters configuration & mappings
  const rawMapping = (node.config?.mapping as WorkflowMapping | undefined)?.values ?? {};
  const [activeMappingField, setActiveMappingField] = React.useState<string | null>(null);

  const handleNameChange = (newName: string) => {
    const updatedMetadata = {
      ...(node.metadata ?? {}),
      [STEP_METADATA_KEY]: {
        ...stepMetadata,
        name: newName,
      },
    };
    onUpdateNode({ ...node, metadata: updatedMetadata });
  };

  const handleRoleChange = (newRole: string) => {
    const updatedMetadata = {
      ...(node.metadata ?? {}),
      [STEP_METADATA_KEY]: {
        ...stepMetadata,
        is_approval_step: true,
        required_approval_role: newRole,
      },
    };
    // Ensure capability_name is strictly null on approval node (Correction 1)
    onUpdateNode({ ...node, capabilityName: null, metadata: updatedMetadata });
  };

  const handleParameterChange = (key: string, val: unknown) => {
    const newConfig = { ...node.config, [key]: val };
    onUpdateNode({ ...node, config: newConfig });
  };

  const handleMappingChange = (key: string, mappingVal: WorkflowValue) => {
    const currentValues = { ...(node.config?.mapping as WorkflowMapping | undefined)?.values };
    currentValues[key] = mappingVal;
    const newConfig = {
      ...node.config,
      mapping: { values: currentValues },
    };
    onUpdateNode({ ...node, config: newConfig });
  };

  const handleClearMapping = (key: string) => {
    const currentValues = { ...(node.config?.mapping as WorkflowMapping | undefined)?.values };
    delete currentValues[key];
    const newConfig = { ...node.config };
    if (Object.keys(currentValues).length === 0) {
      delete newConfig.mapping;
    } else {
      newConfig.mapping = { values: currentValues };
    }
    onUpdateNode({ ...node, config: newConfig });
    setActiveMappingField(null);
  };

  const properties = (capability?.parametersSchema?.properties as Record<string, Record<string, unknown>> | undefined) ?? {};
  const requiredFields = new Set((capability?.parametersSchema?.required as string[] | undefined) ?? []);

  return (
    <aside
      aria-label="Node Inspector"
      className="w-84 border-l border-border bg-card/60 backdrop-blur flex flex-col h-full overflow-hidden"
      data-testid="node-inspector"
    >
      <div className="p-3 border-b border-border flex items-center justify-between">
        <div>
          <span className="text-xs font-semibold text-foreground">Step Inspector</span>
          <span className="text-[10px] text-muted-foreground ml-2 font-mono">
            {node.nodeId}
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClose}
          className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
          data-testid="node-inspector-close"
        >
          ✕
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Step Name */}
        <div className="space-y-1">
          <label className="text-[11px] font-medium text-foreground">Step Label</label>
          <Input
            value={stepName}
            onChange={(e) => handleNameChange(e.target.value)}
            className="h-8 text-xs"
            data-testid="node-inspector-name-input"
          />
        </div>

        {/* Approval Gate Configuration (Correction 1) */}
        {isApprovalNode ? (
          <div className="space-y-3 p-3 rounded-lg border border-amber-500/30 bg-amber-500/5">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">
                Pure Wait Gate
              </Badge>
              <span className="text-xs text-amber-500 font-medium">Human Governance</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              This step halts execution until an authorized operator makes a decision in the Approval Queue. No automated capability is executed.
            </p>
            <div className="space-y-1">
              <label className="text-[11px] font-medium text-foreground">Required Role</label>
              <Input
                value={requiredRole}
                onChange={(e) => handleRoleChange(e.target.value)}
                placeholder="e.g. operator or finance:manager"
                className="h-8 text-xs"
                data-testid="node-inspector-approval-role-input"
              />
            </div>
          </div>
        ) : (
          /* Capability Parameters Configuration */
          <div className="space-y-3">
            <div className="p-2.5 rounded-lg border border-border bg-muted/40 space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground truncate">
                  {capability?.name ?? node.capabilityName}
                </span>
                <Badge variant={capability?.isReadOnly ? "secondary" : "default"} className="text-[9px] px-1 py-0">
                  {capability?.isReadOnly ? "Read" : "Action"}
                </Badge>
              </div>
              {capability?.description && (
                <p className="text-[11px] text-muted-foreground">
                  {capability.description}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-foreground">Parameters</span>
                <span className="text-[10px] text-muted-foreground">
                  {Object.keys(properties).length} configured
                </span>
              </div>

              {Object.keys(properties).length === 0 ? (
                <p className="text-xs text-muted-foreground italic">
                  This capability declares no parameters.
                </p>
              ) : (
                Object.entries(properties).map(([key, propSchema]) => {
                  const isRequired = requiredFields.has(key);
                  const isMapped = key in rawMapping;
                  const staticValue = node.config?.[key];
                  const isEditingMapping = activeMappingField === key;

                  return (
                    <div key={key} className="space-y-1 border-b border-border/50 pb-2.5">
                      <div className="flex items-center justify-between">
                        <label className="text-[11px] font-medium text-foreground font-mono">
                          {key} {isRequired && <span className="text-destructive">*</span>}
                        </label>
                        <Button
                          variant={isMapped ? "secondary" : "ghost"}
                          size="sm"
                          onClick={() => setActiveMappingField(isEditingMapping ? null : key)}
                          className={`h-5 text-[10px] px-1.5 ${isMapped ? "bg-primary/20 text-primary border border-primary/30" : "text-muted-foreground"}`}
                          data-testid={`node-toggle-mapping-${key}`}
                        >
                          {isMapped ? "Mapped (F3)" : "Map data"}
                        </Button>
                      </div>

                      {Boolean(propSchema.description) && (
                        <p className="text-[10px] text-muted-foreground">
                          {String(propSchema.description)}
                        </p>
                      )}

                      {/* Render Mapping Editor if active or already mapped */}
                      {isEditingMapping || isMapped ? (
                        <MappingEditor
                          fieldName={key}
                          fieldSchema={propSchema}
                          value={rawMapping[key]}
                          ancestors={ancestors}
                          onChange={(newVal) => handleMappingChange(key, newVal)}
                          onClear={() => handleClearMapping(key)}
                        />
                      ) : (
                        /* Static value input */
                        <Input
                          value={String(staticValue ?? "")}
                          onChange={(e) => handleParameterChange(key, e.target.value)}
                          placeholder={propSchema.type === "object" ? '{"key": "value"}' : `Enter ${key}...`}
                          className="h-8 text-xs font-mono"
                          data-testid={`node-param-input-${key}`}
                        />
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* Delete Step Button */}
        <div className="pt-2 border-t border-border">
          <Button
            variant="destructive"
            size="sm"
            onClick={() => onDeleteNode(node.nodeId)}
            className="w-full text-xs h-8"
            data-testid="node-inspector-delete-button"
          >
            Remove Step
          </Button>
        </div>
      </div>
    </aside>
  );
}
