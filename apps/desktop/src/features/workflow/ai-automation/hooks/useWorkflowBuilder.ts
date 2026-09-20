import { useCallback, useState } from "react";
import {
  correctWorkflowProposal,
  createWorkflowDraft,
  generateWorkflowProposal,
  publishWorkflowDraft,
  updateWorkflowDraft,
  validateWorkflowDraft,
} from "../api";
import { MAX_CORRECTION_ATTEMPTS } from "../types";
import type { BuilderFlowPhase, WorkflowBuilderProposal, WorkflowDraftHandle } from "../types";

/**
 * Drives the AI Workflow Builder flow end to end, owning the client-side confirmation and
 * bounded-correction state the backend deliberately does not own (D9/D10):
 *
 *   generate -> [review] -> createDraft (explicit user action) -> validate
 *     -> (invalid) -> correct (bounded, max 3) -> update -> validate -> ...
 *     -> (valid)   -> [human review] -> publish (explicit user action)
 *
 * This hook never treats a proposal as persisted until `createDraft()` is explicitly called, and
 * never calls `publish` except from the explicit `publish()` action below -- there is no code
 * path here that autonomously advances past either confirmation gate.
 */
export function useWorkflowBuilder() {
  const [phase, setPhase] = useState<BuilderFlowPhase>("idle");
  const [proposal, setProposal] = useState<WorkflowBuilderProposal | null>(null);
  const [draft, setDraft] = useState<WorkflowDraftHandle | null>(null);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [validationWarnings, setValidationWarnings] = useState<string[]>([]);
  const [correctionAttempt, setCorrectionAttempt] = useState(0);
  const [intent, setIntent] = useState("");
  const [error, setError] = useState<string | null>(null);

  const generate = useCallback(async (userIntent: string) => {
    setIntent(userIntent);
    setPhase("generating");
    setError(null);
    setDraft(null);
    setCorrectionAttempt(0);
    try {
      const result = await generateWorkflowProposal(userIntent);
      setProposal(result);
      setPhase(result.status === "proposed" ? "proposed" : "generation_failed");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("failed");
    }
  }, []);

  /** Explicit, user-triggered persistence of the current proposal as an F4 DRAFT -- the
   * confirmation gate D10 requires between "a proposal exists" and "anything is persisted". */
  const createDraft = useCallback(async () => {
    if (!proposal?.graph) {
      return;
    }
    setPhase("creating_draft");
    setError(null);
    try {
      const handle = await createWorkflowDraft(proposal.name, proposal.description, proposal.graph.raw);
      setDraft(handle);
      setPhase("validating");
      const report = await validateWorkflowDraft(handle.definitionId);
      setValidationErrors(report.errors);
      setValidationWarnings(report.warnings);
      setPhase(report.isValid ? "ready_for_review" : "correcting");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("failed");
    }
  }, [proposal]);

  /** One bounded correction round trip: ask the AI Workflow Builder for a corrected proposal
   * given F4's own real validation errors, then apply it via `update` + `validate` again. */
  const correct = useCallback(async () => {
    if (!draft || !proposal?.graph) {
      return;
    }
    if (correctionAttempt >= MAX_CORRECTION_ATTEMPTS) {
      setPhase("correction_exhausted");
      return;
    }
    setPhase("correcting");
    setError(null);
    try {
      const nextAttempt = correctionAttempt + 1;
      const corrected = await correctWorkflowProposal(
        intent,
        proposal.conversationId,
        proposal.graph.raw,
        validationErrors,
        validationWarnings,
      );
      setProposal(corrected);
      setCorrectionAttempt(nextAttempt);
      if (corrected.status !== "proposed" || !corrected.graph) {
        setValidationErrors(corrected.errors);
        setValidationWarnings(corrected.warnings);
        if (nextAttempt >= MAX_CORRECTION_ATTEMPTS) {
          setPhase("correction_exhausted");
        } else {
          setPhase("correcting");
        }
        return;
      }
      const updated = await updateWorkflowDraft(draft.definitionId, draft.lockVersion, corrected.graph.raw);
      setDraft(updated);
      const report = await validateWorkflowDraft(updated.definitionId);
      setValidationErrors(report.errors);
      setValidationWarnings(report.warnings);
      if (report.isValid) {
        setPhase("ready_for_review");
      } else if (nextAttempt >= MAX_CORRECTION_ATTEMPTS) {
        setPhase("correction_exhausted");
      } else {
        setPhase("correcting");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("failed");
    }
  }, [draft, proposal, intent, validationErrors, validationWarnings, correctionAttempt]);

  /** Explicit, user-triggered publish -- the human-approval boundary (D9). Never called from
   * `generate`/`createDraft`/`correct` above; only ever from a direct user action. */
  const publish = useCallback(async () => {
    if (!draft) {
      return;
    }
    setPhase("publishing");
    setError(null);
    try {
      const published = await publishWorkflowDraft(draft.definitionId, draft.lockVersion);
      setDraft(published);
      setPhase("published");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("failed");
    }
  }, [draft]);

  const reset = useCallback(() => {
    setPhase("idle");
    setProposal(null);
    setDraft(null);
    setValidationErrors([]);
    setValidationWarnings([]);
    setCorrectionAttempt(0);
    setIntent("");
    setError(null);
  }, []);

  return {
    phase,
    proposal,
    draft,
    validationErrors,
    validationWarnings,
    correctionAttempt,
    maxCorrectionAttempts: MAX_CORRECTION_ATTEMPTS,
    error,
    generate,
    createDraft,
    correct,
    publish,
    reset,
  };
}
