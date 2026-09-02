"""
KORTEX Recipe Engine Pydantic v2 Domain Models.

Defines all recipe manifest models, DSL components, package descriptors,
compatibility settings, and lifecycle operation result models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from kortex.engines.workflow.models import WorkflowDefinition


class RecipeInput(BaseModel):
    """Declarative input argument parameter for a recipe."""

    name: str = Field(..., description="Parameter variable name")
    type: str = Field("string", description="Parameter data type (string, integer, boolean, object, array)")
    description: str = Field("", description="Human-readable parameter description")
    required: bool = Field(True, description="True if input is mandatory")
    default: Any | None = Field(None, description="Default value if not provided")


class RecipeStep(BaseModel):
    """Individual declarative step specification within a recipe."""

    id: str = Field(..., description="Step unique identifier within recipe")
    name: str = Field(..., description="Human-readable step title")
    capability: str | None = Field(
        None, description="Canonical capability name to invoke (kortex.<domain>.<resource>.<action>)"
    )
    parameters: dict[str, Any] = Field(default_factory=dict, description="Step parameters and variable bindings")
    is_approval: bool = Field(False, description="True if step requires human approval")
    approval_role: str | None = Field(None, description="Role authorized to approve")
    retry_attempts: int | None = Field(None, description="Max retry attempts for step")
    retry_backoff: float | None = Field(None, description="Backoff multiplier")
    on_failure_continue: bool = Field(
        False, description="If True, step failure will not halt recipe compilation/workflow"
    )
    compensation: dict[str, Any] | None = Field(None, description="Rollback compensation parameters")


class RecipeOutput(BaseModel):
    """Declarative output parameter defined by a recipe."""

    name: str = Field(..., description="Output variable name")
    type: str = Field("string", description="Data type of output")
    value_expression: str = Field("", description="Expression or step output reference binding")
    description: str = Field("", description="Output parameter description")


class RecipeSettings(BaseModel):
    """Execution and configuration settings for a recipe."""

    timeout_seconds: int = Field(3600, ge=1, description="Execution timeout limit in seconds")
    priority: str = Field("NORMAL", description="Execution priority (LOW, NORMAL, HIGH, CRITICAL)")
    trigger: str = Field("MANUAL", description="Trigger source (MANUAL, EVENT, SCHEDULED, API)")


class RecipePermission(BaseModel):
    """Permission requirement model for capability execution least privilege."""

    resource: str = Field(..., description="Target resource or namespace")
    action: str = Field(..., description="Required operation/action")
    scope: str | None = Field(None, description="Permission scope constraint")


class RecipeCompatibility(BaseModel):
    """Engine and Kernel version compatibility declarations."""

    kernel: str = Field(">=0.1.0", description="Kernel SemVer requirement")
    workflow_engine: str = Field(">=0.1.0", description="Workflow Engine SemVer requirement")
    storage_engine: str = Field(">=0.1.0", description="Storage Engine SemVer requirement")
    document_engine: str | None = Field(None, description="Document Engine SemVer requirement")
    connector_engine: str | None = Field(None, description="Connector Engine SemVer requirement")
    module_versions: dict[str, str] = Field(default_factory=dict, description="Module version dependencies")


class RecipeDependency(BaseModel):
    """Dependency declaration on another recipe or module."""

    name: str = Field(..., description="Dependency identifier or namespace")
    version: str = Field(..., description="SemVer requirement constraint")
    optional: bool = Field(False, description="True if dependency is optional")


class RecipeProfile(BaseModel):
    """Execution profile or environment parameter preset."""

    name: str = Field(..., description="Profile identifier (e.g. dev, staging, prod)")
    settings: dict[str, Any] = Field(default_factory=dict, description="Profile specific parameter overrides")


class RecipeMetadata(BaseModel):
    """Recipe asset metadata information payload."""

    id: str = Field(..., description="Recipe asset UUID")
    name: str = Field(..., description="Recipe name")
    namespace: str = Field(..., description="Canonical namespace identifier")
    version: str = Field(..., description="Semantic version string")
    description: str = Field("", description="Detailed recipe description")
    author: str = Field("", description="Author or organization name")
    organization: str | None = Field(None, description="Vendor organization")
    created_at: str = Field("", description="Recipe creation ISO timestamp")


class RecipeManifest(BaseModel):
    """Canonical Recipe Asset Manifest definition."""

    id: str = Field(..., description="Recipe asset UUID")
    name: str = Field(..., description="Human readable recipe title")
    namespace: str = Field(..., description="Reverse domain identifier (e.g. kortex.hr.payroll)")
    version: str = Field("1.0.0", description="Semantic Version string")
    description: str = Field("", description="Recipe purpose and scope")
    author: dict[str, Any] = Field(default_factory=dict, description="Author contact details")
    license: str = Field("MIT", description="License model")
    dependencies: dict[str, str] = Field(
        default_factory=dict, description="Map of required assets and version constraints"
    )
    capabilities_required: list[str] = Field(default_factory=list, description="Capabilities invoked by recipe")
    capabilities_provided: list[str] = Field(default_factory=list, description="Capabilities exposed by recipe")
    permissions_required: list[str] = Field(default_factory=list, description="Required permissions")
    kernel_compatibility: str = Field(">=0.1.0", description="Compatible Kernel SemVer constraint")
    checksum: str = Field("", description="SHA256 checksum hash")
    signature: str | None = Field(None, description="Optional digital signature")


class RecipeDefinition(BaseModel):
    """Complete declarative Recipe Definition specification object."""

    manifest: RecipeManifest = Field(..., description="Associated asset manifest")
    inputs: list[RecipeInput] = Field(default_factory=list, description="Declared input arguments")
    steps: list[RecipeStep] = Field(default_factory=list, description="Declared execution steps")
    outputs: list[RecipeOutput] = Field(default_factory=list, description="Declared output definitions")
    settings: RecipeSettings = Field(default_factory=RecipeSettings, description="Recipe settings")
    permissions: list[RecipePermission] = Field(default_factory=list, description="Permissions list")
    compatibility: RecipeCompatibility = Field(
        default_factory=RecipeCompatibility, description="Compatibility specifications"
    )


class RecipePackage(BaseModel):
    """Standalone .kortex-recipe package payload container."""

    package_id: str = Field(..., description="Package identifier")
    file_name: str = Field(..., description="Package archive filename (.kortex-recipe)")
    checksum: str = Field(..., description="SHA256 payload checksum hash")
    payload_bytes: bytes = Field(..., description="Raw ZIP package archive binary content")
    signature: str | None = Field(None, description="Digital signature placeholder")


class RecipeCompilationResult(BaseModel):
    """Outcome payload from compiling a RecipeDefinition into a WorkflowDefinition."""

    success: bool = Field(..., description="True if recipe compiled successfully")
    recipe_id: str = Field(..., description="Target recipe ID")
    workflow_definition: WorkflowDefinition | None = Field(None, description="Compiled executable workflow definition")
    errors: list[str] = Field(default_factory=list, description="Compilation errors if any")


class RecipeValidationResult(BaseModel):
    """Outcome payload from validating a recipe structure and security rules."""

    is_valid: bool = Field(..., description="True if recipe is structurally and securely valid")
    recipe_id: str = Field(..., description="Target recipe ID")
    errors: list[str] = Field(default_factory=list, description="Validation failure error messages")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal validation warnings")


class RecipeInstallationResult(BaseModel):
    """Outcome payload from installing a recipe into the system environment."""

    success: bool = Field(..., description="True if installation succeeded")
    recipe_id: str = Field(..., description="Installed recipe ID")
    version: str = Field(..., description="Installed version string")
    installed_at: str = Field("", description="Installation ISO timestamp")
    errors: list[str] = Field(default_factory=list, description="Installation errors if any")


class RecipeUpgradeResult(BaseModel):
    """Outcome payload from upgrading an existing installed recipe."""

    success: bool = Field(..., description="True if upgrade succeeded")
    recipe_id: str = Field(..., description="Upgraded recipe ID")
    previous_version: str = Field(..., description="Version prior to upgrade")
    new_version: str = Field(..., description="Upgraded version string")
    errors: list[str] = Field(default_factory=list, description="Upgrade errors if any")


class RecipeRemovalResult(BaseModel):
    """Outcome payload from removing an installed recipe."""

    success: bool = Field(..., description="True if removal succeeded")
    recipe_id: str = Field(..., description="Removed recipe ID")
    version: str = Field(..., description="Removed version string")
    removed_at: str = Field("", description="Removal ISO timestamp")
    errors: list[str] = Field(default_factory=list, description="Removal errors if any")
