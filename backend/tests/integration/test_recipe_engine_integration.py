"""
Integration test for Recipe Engine to Workflow Engine pipeline.

Verifies end-to-end flow:
Recipe Engine parses and compiles declarative recipe YAML -> outputs WorkflowDefinition ->
Workflow Engine receives and executes WorkflowDefinition state machine.
Recipe Engine NEVER executes recipes directly.
"""

import pytest
from kortex.engines.recipe.engine import RecipeEngine
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import WorkflowState


SAMPLE_RECIPE_INTEGRATION_YAML = """
manifest:
  id: "recipe-integ-01"
  name: "Integration Test Recipe"
  namespace: "kortex.integration.recipe"
  version: "1.0.0"
  description: "Validates Recipe Engine to Workflow Engine pipeline"
  checksum: "d41d8cd98f00b204e9800998ecf8427e"

inputs:
  - name: "param_a"
    type: "string"
    required: true

steps:
  - id: "step_1"
    name: "First Action"
    parameters:
      val: "${inputs.param_a}"
  - id: "step_2"
    name: "Second Action"

settings:
  timeout_seconds: 600
  priority: "NORMAL"
"""


@pytest.mark.asyncio
async def test_recipe_to_workflow_engine_pipeline() -> None:
    recipe_engine = RecipeEngine()
    workflow_engine = WorkflowEngine()

    # 1. Parse recipe specification
    recipe_def = recipe_engine.parse(SAMPLE_RECIPE_INTEGRATION_YAML)
    assert recipe_def.manifest.id == "recipe-integ-01"

    # 2. Validate recipe specification
    val_res = recipe_engine.validate(recipe_def)
    assert val_res.is_valid is True

    # 3. Compile recipe specification into WorkflowDefinition
    inputs = {"param_a": "hello_world"}
    comp_res = recipe_engine.compile(recipe_def, inputs)
    assert comp_res.success is True
    assert comp_res.workflow_definition is not None

    compiled_wf_def = comp_res.workflow_definition
    assert compiled_wf_def.id == "wf_recipe-integ-01"
    assert len(compiled_wf_def.steps) == 2
    assert compiled_wf_def.steps[0].parameters["val"] == "hello_world"

    # Execute workflow steps in Workflow Engine
    exec_res = await workflow_engine.execute_workflow(compiled_wf_def)
    assert exec_res.state == WorkflowState.COMPLETED
