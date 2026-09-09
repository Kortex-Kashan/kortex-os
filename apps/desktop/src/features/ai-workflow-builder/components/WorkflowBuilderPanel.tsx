import * as React from "react";
import { Badge, Button, Textarea } from "@kortex/design-system";
import { useWorkflowBuilder } from "../hooks/useWorkflowBuilder";
import { PHASE_LABELS } from "../api";

/**
 * AI Workflow Builder review/confirm surface (master prompt §19).
 *
 * Deliberately NOT a visual canvas or drag-and-drop editor -- that is the separate, later Visual
 * Canvas + Manual Builder milestone. This panel shows what the AI proposed, lets the user
 * explicitly confirm each governed step (Generate -> Create Draft -> [correct] -> Approve &
 * Publish), and makes those three actions visually and functionally impossible to confuse: each
 * has its own distinct, explicitly-labeled button, and none fires as a side effect of another.
 */
export function WorkflowBuilderPanel(): React.JSX.Element {
  const {
    phase,
    proposal,
    draft,
    validationErrors,
    validationWarnings,
    correctionAttempt,
    maxCorrectionAttempts,
    error,
    generate,
    createDraft,
    correct,
    publish,
    reset,
  } = useWorkflowBuilder();
  const [intentInput, setIntentInput] = React.useState("");

  const busy = phase === "generating" || phase === "creating_draft" || phase === "validating" || phase === "publishing";

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="ai-workflow-builder-panel">
      <div>
        <h2 className="text-lg font-semibold">AI Workflow Builder</h2>
        <p className="text-sm text-muted-foreground" data-testid="builder-phase-label">
          {PHASE_LABELS[phase]}
        </p>
      </div>

      {/* 1. Natural-language workflow request */}
      {phase === "idle" || phase === "failed" ? (
        <div className="flex flex-col gap-2">
          <Textarea
            value={intentInput}
            onChange={(event) => setIntentInput(event.target.value)}
            placeholder="Describe the automation you want, e.g. 'Look up invoice INV-1 and send a webhook about it.'"
            data-testid="builder-intent-input"
          />
          <Button
            onClick={() => void generate(intentInput)}
            disabled={!intentInput.trim()}
            data-testid="builder-generate-button"
          >
            Generate
          </Button>
        </div>
      ) : null}

      {error ? (
        <div className="rounded border border-destructive/40 bg-destructive/10 p-2 text-sm" data-testid="builder-error">
          {error}
        </div>
      ) : null}

      {/* 2-5. Generated workflow summary, node/step list, capability per node, key parameters/mappings */}
      {proposal?.graph ? (
        <div className="flex flex-col gap-2 rounded border p-3" data-testid="builder-proposal-summary">
          <div>
            <span className="font-medium">{proposal.name || "(untitled workflow)"}</span>
            {proposal.description ? (
              <p className="text-sm text-muted-foreground">{proposal.description}</p>
            ) : null}
          </div>
          <ol className="flex flex-col gap-2">
            {proposal.graph.nodes.map((node, index) => (
              <li key={node.nodeId} className="rounded border p-2 text-sm" data-testid="builder-node-row">
                <div className="flex items-center justify-between">
                  <span>
                    {index + 1}. {node.nodeId}
                  </span>
                  {node.isApprovalStep ? <Badge variant="outline">Requires approval to run</Badge> : null}
                </div>
                <div className="text-muted-foreground">{node.capabilityName ?? "(no capability)"}</div>
                {Object.keys(node.config).length > 0 ? (
                  <pre className="mt-1 overflow-x-auto rounded bg-muted p-1 text-xs">
                    {JSON.stringify(node.config, null, 2)}
                  </pre>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {/* 6-7. Validation status, errors/warnings */}
      {phase === "correcting" || phase === "ready_for_review" || phase === "correction_exhausted" ? (
        <div className="flex flex-col gap-1" data-testid="builder-validation-status">
          <Badge variant={validationErrors.length === 0 ? "default" : "destructive"}>
            {validationErrors.length === 0 ? "Valid" : `${validationErrors.length} error(s)`}
          </Badge>
          {validationErrors.map((message, index) => (
            <p key={`error-${index}`} className="text-sm text-destructive">
              {message}
            </p>
          ))}
          {validationWarnings.map((message, index) => (
            <p key={`warning-${index}`} className="text-sm text-amber-600">
              {message}
            </p>
          ))}
        </div>
      ) : null}

      {/* 8. Correction state */}
      {phase === "correcting" || phase === "correction_exhausted" ? (
        <div className="text-sm" data-testid="builder-correction-state">
          Correction attempt {correctionAttempt} of {maxCorrectionAttempts}
          {phase === "correction_exhausted" ? " -- could not produce a valid workflow. Try rephrasing your request." : null}
        </div>
      ) : null}
      {phase === "correcting" && validationErrors.length > 0 && correctionAttempt < maxCorrectionAttempts ? (
        <Button variant="secondary" onClick={() => void correct()} data-testid="builder-correct-button">
          Ask AI to correct
        </Button>
      ) : null}

      {/* 9. Explicit "Create Draft" confirmation -- distinct from Generate and from Publish */}
      {phase === "proposed" ? (
        <Button onClick={() => void createDraft()} data-testid="builder-create-draft-button">
          Create Draft
        </Button>
      ) : null}
      {phase === "generation_failed" ? (
        <div className="flex flex-col gap-1" data-testid="builder-generation-failed">
          {proposal?.errors.map((message, index) => (
            <p key={index} className="text-sm text-destructive">
              {message}
            </p>
          ))}
          <Button variant="secondary" onClick={reset} data-testid="builder-try-again-button">
            Try again
          </Button>
        </div>
      ) : null}

      {/* 10-11. Human review + explicit "Approve & Publish" */}
      {phase === "ready_for_review" && draft ? (
        <div className="flex flex-col gap-2 rounded border p-3" data-testid="builder-human-review">
          <p className="text-sm">
            Review the draft above, then approve publication. Publishing makes this workflow
            executable and is never performed automatically by the AI.
          </p>
          <Button onClick={() => void publish()} data-testid="builder-approve-publish-button">
            Approve &amp; Publish
          </Button>
        </div>
      ) : null}

      {/* 12. Success/failure state */}
      {phase === "published" && draft ? (
        <div className="rounded border border-green-500/40 bg-green-500/10 p-2 text-sm" data-testid="builder-published">
          Published as definition {draft.definitionId} (version reflected in status: {draft.status}).
          <div>
            <Button variant="secondary" onClick={reset} data-testid="builder-build-another-button">
              Build another
            </Button>
          </div>
        </div>
      ) : null}
      {phase === "failed" && !busy ? (
        <Button variant="secondary" onClick={reset} data-testid="builder-reset-button">
          Start over
        </Button>
      ) : null}
    </div>
  );
}
