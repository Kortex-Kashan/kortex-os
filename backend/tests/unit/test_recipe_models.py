"""
Unit tests for KORTEX Recipe Engine Pydantic v2 domain models.
"""

from kortex.engines.recipe.models import (
    RecipeCompatibility,
    RecipeDefinition,
    RecipeDependency,
    RecipeInput,
    RecipeManifest,
    RecipeMetadata,
    RecipeOutput,
    RecipePermission,
    RecipeProfile,
    RecipeSettings,
    RecipeStep,
)


def test_recipe_input_model() -> None:
    inp = RecipeInput(name="employee_id", type="string", description="Target employee ID", required=True)
    assert inp.name == "employee_id"
    assert inp.type == "string"
    assert inp.required is True
    assert inp.default is None


def test_recipe_step_model() -> None:
    step = RecipeStep(
        id="step_1",
        name="Process Payroll",
        capability="kortex.hr.payroll.process",
        parameters={"currency": "USD"},
        is_approval=True,
        approval_role="finance_manager",
        retry_attempts=3,
        retry_backoff=2.0,
    )
    assert step.id == "step_1"
    assert step.is_approval is True
    assert step.approval_role == "finance_manager"
    assert step.retry_attempts == 3


def test_recipe_manifest_model() -> None:
    manifest = RecipeManifest(
        id="recipe-001",
        name="Payroll Processing Recipe",
        namespace="kortex.hr.payroll",
        version="1.0.0",
        description="Processes monthly payroll",
        capabilities_required=["kortex.storage.data.session"],
        capabilities_provided=["kortex.hr.payroll.run"],
        checksum="abcd1234efgh5678",
    )
    assert manifest.id == "recipe-001"
    assert manifest.namespace == "kortex.hr.payroll"
    assert manifest.version == "1.0.0"


def test_recipe_definition_model() -> None:
    manifest = RecipeManifest(
        id="rec-100",
        name="Test Recipe",
        namespace="kortex.test",
        version="1.0.0",
        checksum="123",
    )
    step = RecipeStep(id="s1", name="Step One")
    def_model = RecipeDefinition(
        manifest=manifest,
        steps=[step],
        inputs=[RecipeInput(name="arg1")],
        outputs=[RecipeOutput(name="res1")],
        settings=RecipeSettings(timeout_seconds=1800),
        permissions=[RecipePermission(resource="hr", action="read")],
        compatibility=RecipeCompatibility(kernel=">=0.1.0"),
    )
    assert def_model.manifest.id == "rec-100"
    assert len(def_model.steps) == 1
    assert def_model.settings.timeout_seconds == 1800


def test_recipe_auxiliary_models() -> None:
    meta = RecipeMetadata(id="m1", name="n1", namespace="kortex.meta", version="1.0.0")
    assert meta.id == "m1"

    dep = RecipeDependency(name="kortex.kernel", version=">=0.1.0")
    assert dep.name == "kortex.kernel"

    prof = RecipeProfile(name="dev", settings={"debug": True})
    assert prof.name == "dev"
