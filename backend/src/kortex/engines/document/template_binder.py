"""Hybrid Data Binding Engine for KORTEX OS Document Engine.

This module implements TemplateBinder, which is responsible for binding validated
business and context data to a declarative TemplateSchema without executing dynamic scripts,
in accordance with Section 8.2 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.models import (
    BindingContext,
    TemplateSchema,
    ValidationReport,
)
from kortex.engines.document.template_library import TemplateLibrary

# Regular expression for safe placeholder identifier validation
PLACEHOLDER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


class BindingResult(BaseModel):
    """Deterministic result payload produced by binding context data to a TemplateSchema."""

    model_config = ConfigDict(frozen=True)

    template_id: str
    version: str
    resolved_values: dict[str, Any] = Field(default_factory=dict)
    unresolved_placeholders: list[str] = Field(default_factory=list)
    validation_report: ValidationReport


def resolve_dotted_path(data: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Safely resolve a dotted-path lookup (e.g. 'employee.department.name') in a nested dictionary.

    Args:
        data: The dictionary to search.
        path: Key or dotted-path string to look up.

    Returns:
        Tuple of (found: bool, value: Any).
    """
    if not isinstance(data, dict) or not path:
        return (False, None)

    # Direct key lookup first
    if path in data:
        return (True, data[path])

    # Dotted path traversal
    parts = path.split(".")
    current: Any = data

    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return (False, None)

    return (True, current)


class TemplateBinder:
    """Pure data-binding component for resolving template placeholders against BindingContext.

    Guarantees:
    1. Immutability: TemplateSchema and BindingContext are never mutated.
    2. Determinism: Identical inputs produce identical BindingResult outputs.
    3. Security: Pure dict traversal; zero eval(), exec(), dynamic code execution, or I/O.
    4. Safety: Dotted-path navigation without unrestricted expression evaluation.
    """

    def __init__(self, template_library: TemplateLibrary | None = None) -> None:
        """Initialize TemplateBinder with a TemplateLibrary instance.

        Args:
            template_library: Optional TemplateLibrary instance. Defaults to new library.
        """
        self._library = template_library if template_library is not None else TemplateLibrary()

    @property
    def library(self) -> TemplateLibrary:
        """Return the underlying TemplateLibrary instance."""
        return self._library

    async def bind(
        self, schema: TemplateSchema, context: BindingContext
    ) -> ValidationReport:
        """Validate context data against template placeholders (ITemplateBinder protocol).

        Args:
            schema: TemplateSchema definition.
            context: BindingContext containing input data and computed fields.

        Returns:
            ValidationReport summarizing binding outcome.
        """
        result = await self.bind_schema(schema, context)
        return result.validation_report

    async def bind_template(
        self, template_id: str, context: BindingContext, version: str | None = None
    ) -> BindingResult:
        """Resolve a template from TemplateLibrary and bind context data against it.

        Args:
            template_id: Template schema identifier string.
            context: BindingContext data.
            version: Optional SemVer string. Resolves latest version if omitted.

        Returns:
            BindingResult payload.

        Raises:
            DocumentTemplateError: If template_id or version is not found in library.
        """
        schema = await self._library.get_template(template_id, version=version)
        return await self.bind_schema(schema, context)

    async def bind_schema(
        self, schema: TemplateSchema, context: BindingContext
    ) -> BindingResult:
        """Bind context data directly against a provided TemplateSchema.

        Args:
            schema: TemplateSchema definition.
            context: BindingContext data.

        Returns:
            BindingResult payload containing resolved values and ValidationReport.

        Raises:
            DocumentTemplateError: If schema or context input is invalid.
        """
        if schema is None:
            raise DocumentTemplateError("TemplateSchema input cannot be None.")
        if context is None:
            raise DocumentTemplateError("BindingContext input cannot be None.")

        resolved_values: dict[str, Any] = {}
        unresolved_placeholders: list[str] = []
        missing_placeholders: list[str] = []
        type_mismatches: list[str] = []
        errors: list[str] = []
        warnings: list[str] = []
        computed_fields_resolved: list[str] = []

        # Combine all target placeholders (schema.placeholders + schema.required_fields)
        all_target_placeholders: set[str] = set()
        if schema.placeholders:
            all_target_placeholders.update(schema.placeholders)
        if schema.required_fields:
            all_target_placeholders.update(schema.required_fields)

        required_set = set(schema.required_fields) if schema.required_fields else set()

        for ph in sorted(all_target_placeholders):
            # Validate placeholder identifier format
            if not ph or not isinstance(ph, str) or not PLACEHOLDER_REGEX.match(ph.strip()):
                msg = f"Invalid placeholder identifier: '{ph}'."
                errors.append(msg)
                missing_placeholders.append(ph)
                continue

            ph_clean = ph.strip()
            found = False
            val: Any = None

            # Priority 1: Check BindingContext.computed_fields
            if context.computed_fields:
                found_computed, comp_val = resolve_dotted_path(context.computed_fields, ph_clean)
                if found_computed:
                    found = True
                    val = comp_val
                    computed_fields_resolved.append(ph_clean)

            # Priority 2: Check BindingContext.data if not resolved in computed_fields
            if not found and context.data:
                found_data, data_val = resolve_dotted_path(context.data, ph_clean)
                if found_data:
                    found = True
                    val = data_val

            if found and val is not None:
                resolved_values[ph_clean] = val
            else:
                unresolved_placeholders.append(ph_clean)
                missing_placeholders.append(ph_clean)

                if ph_clean in required_set:
                    err_msg = f"Missing required field: '{ph_clean}'."
                    errors.append(err_msg)
                else:
                    warn_msg = f"Missing optional placeholder: '{ph_clean}'."
                    warnings.append(warn_msg)

        is_valid = len(errors) == 0

        validation_report = ValidationReport(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            missing_placeholders=sorted(set(missing_placeholders)),
            type_mismatches=type_mismatches,
            computed_fields_resolved=sorted(set(computed_fields_resolved)),
        )

        return BindingResult(
            template_id=schema.template_id,
            version=schema.version,
            resolved_values=resolved_values,
            unresolved_placeholders=sorted(set(unresolved_placeholders)),
            validation_report=validation_report,
        )


__all__ = ["BindingResult", "TemplateBinder", "resolve_dotted_path"]
