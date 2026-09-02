"""
KORTEX Recipe Engine Compiler Implementation.

Transforms declarative Recipe definitions into executable WorkflowDefinition objects.
Enforces pure determinism:
- Same input + same recipe = identical WorkflowDefinition output.
- No filesystem access.
- No network access.
- No timestamps.
- No randomness (jitter=False).
- No global mutable state.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.recipe.exceptions import RecipeCompilationError
from kortex.engines.recipe.models import RecipeCompilationResult, RecipeDefinition
from kortex.engines.workflow.models import (
    CompensationAction,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowPriority,
    WorkflowStep,
    WorkflowTrigger,
)


class RecipeCompiler:
    """Pure deterministic compiler for Recipe Engine."""

    def compile(
        self,
        recipe: RecipeDefinition,
        input_parameters: dict[str, Any] | None = None,
    ) -> RecipeCompilationResult:
        """Compile a RecipeDefinition into an executable WorkflowDefinition.

        Args:
            recipe: Validated RecipeDefinition model.
            input_parameters: Optional dictionary of input argument overrides.

        Returns:
            RecipeCompilationResult containing the compiled WorkflowDefinition.
        """
        try:
            input_params = input_parameters or {}

            # Validate required recipe inputs
            merged_inputs: dict[str, Any] = {}
            for inp in recipe.inputs:
                if inp.name in input_params:
                    merged_inputs[inp.name] = input_params[inp.name]
                elif inp.default is not None:
                    merged_inputs[inp.name] = inp.default
                elif inp.required:
                    raise RecipeCompilationError(
                        f"Missing mandatory input parameter '{inp.name}' for recipe '{recipe.manifest.id}'."
                    )

            compiled_steps: list[WorkflowStep] = []
            for step in recipe.steps:
                # Interpolate parameters deterministically
                step_params: dict[str, Any] = {}
                for k, v in step.parameters.items():
                    if isinstance(v, str) and v.startswith("${inputs.") and v.endswith("}"):
                        var_name = v[9:-1]
                        step_params[k] = merged_inputs.get(var_name, v)
                    else:
                        step_params[k] = v

                # Build deterministic retry policy
                retry_policy: RetryPolicy | None = None
                if step.retry_attempts is not None:
                    backoff = step.retry_backoff if step.retry_backoff is not None else 2.0
                    retry_policy = RetryPolicy(
                        max_attempts=step.retry_attempts,
                        backoff_factor=backoff,
                        initial_delay_seconds=1.0,
                        jitter=False,  # Enforce pure determinism
                    )

                # Build deterministic compensation action
                compensation_action: CompensationAction | None = None
                if step.compensation:
                    comp_cap = step.compensation.get("capability")
                    comp_params = step.compensation.get("parameters", {})
                    compensation_action = CompensationAction(
                        id=f"compensation_{recipe.manifest.id}_{step.id}",  # Deterministic ID
                        name=f"Rollback {step.name}",
                        capability_name=comp_cap,
                        parameters=comp_params,
                    )

                compiled_step = WorkflowStep(
                    id=step.id,
                    name=step.name,
                    capability_name=step.capability,
                    parameters=step_params,
                    is_approval_step=step.is_approval,
                    required_approval_role=step.approval_role,
                    retry_policy=retry_policy,
                    compensation_action=compensation_action,
                    on_failure_continue=step.on_failure_continue,
                )
                compiled_steps.append(compiled_step)

            # Map trigger source
            trigger_str = recipe.settings.trigger.upper()
            trigger = WorkflowTrigger.RECIPE
            if trigger_str in WorkflowTrigger.__members__:
                trigger = WorkflowTrigger[trigger_str]

            # Map priority
            priority_str = recipe.settings.priority.upper()
            priority = WorkflowPriority.NORMAL
            if priority_str in WorkflowPriority.__members__:
                priority = WorkflowPriority[priority_str]

            workflow_def = WorkflowDefinition(
                id=f"wf_{recipe.manifest.id}",
                name=recipe.manifest.name,
                version=recipe.manifest.version,
                description=recipe.manifest.description,
                steps=compiled_steps,
                trigger=trigger,
                priority=priority,
                timeout_seconds=recipe.settings.timeout_seconds,
            )

            return RecipeCompilationResult(
                success=True,
                recipe_id=recipe.manifest.id,
                workflow_definition=workflow_def,
            )
        except RecipeCompilationError as rce:
            return RecipeCompilationResult(
                success=False,
                recipe_id=recipe.manifest.id,
                errors=[str(rce)],
            )
        except Exception as e:
            return RecipeCompilationResult(
                success=False,
                recipe_id=recipe.manifest.id,
                errors=[f"Compilation unexpected failure: {e}"],
            )
