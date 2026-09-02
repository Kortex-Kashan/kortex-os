"""
Unit tests for KORTEX Recipe Engine Compiler enforcing pure determinism.
"""

from kortex.engines.recipe.compiler import RecipeCompiler
from kortex.engines.recipe.models import (
    RecipeDefinition,
    RecipeInput,
    RecipeManifest,
    RecipeSettings,
    RecipeStep,
)
from kortex.engines.workflow.models import WorkflowPriority, WorkflowTrigger


def test_compiler_deterministic_output() -> None:
    compiler = RecipeCompiler()

    manifest = RecipeManifest(
        id="rec-payroll",
        name="Payroll Engine",
        namespace="kortex.hr.payroll",
        version="1.0.0",
        description="Processes company payroll",
        checksum="12345",
    )
    step1 = RecipeStep(
        id="s1",
        name="Compute Salary",
        capability="kortex.hr.payroll.compute",
        parameters={"period": "${inputs.period}", "bonus": 500},
        retry_attempts=3,
        retry_backoff=2.0,
        compensation={"capability": "kortex.hr.payroll.rollback_compute", "parameters": {"reason": "failed"}},
    )
    step2 = RecipeStep(
        id="s2",
        name="Approve Transfer",
        is_approval=True,
        approval_role="finance_head",
    )
    definition = RecipeDefinition(
        manifest=manifest,
        inputs=[RecipeInput(name="period", type="string", required=True)],
        steps=[step1, step2],
        settings=RecipeSettings(priority="HIGH", trigger="RECIPE", timeout_seconds=1200),
    )

    inputs = {"period": "2026-08"}

    # Execute 1st compilation
    res1 = compiler.compile(definition, inputs)
    assert res1.success is True
    assert res1.workflow_definition is not None

    wf1 = res1.workflow_definition
    assert wf1.id == "wf_rec-payroll"
    assert wf1.priority == WorkflowPriority.HIGH
    assert wf1.trigger == WorkflowTrigger.RECIPE
    assert len(wf1.steps) == 2

    step1_wf = wf1.steps[0]
    assert step1_wf.parameters["period"] == "2026-08"
    assert step1_wf.parameters["bonus"] == 500
    assert step1_wf.retry_policy is not None
    assert step1_wf.retry_policy.jitter is False  # Determinism check
    assert step1_wf.compensation_action is not None
    assert step1_wf.compensation_action.id == "compensation_rec-payroll_s1"  # Deterministic ID

    step2_wf = wf1.steps[1]
    assert step2_wf.is_approval_step is True
    assert step2_wf.required_approval_role == "finance_head"

    # Execute 2nd compilation to verify pure determinism
    res2 = compiler.compile(definition, inputs)
    wf2 = res2.workflow_definition
    assert wf1.model_dump() == wf2.model_dump()


def test_compiler_missing_required_input() -> None:
    compiler = RecipeCompiler()

    manifest = RecipeManifest(
        id="rec-missing",
        name="Missing Input Test",
        namespace="kortex.test.missing",
        version="1.0.0",
        checksum="123",
    )
    definition = RecipeDefinition(
        manifest=manifest,
        inputs=[RecipeInput(name="mandatory_param", required=True)],
        steps=[RecipeStep(id="s1", name="Step")],
    )

    res = compiler.compile(definition, input_parameters={})
    assert res.success is False
    assert any("Missing mandatory input parameter 'mandatory_param'" in err for err in res.errors)
