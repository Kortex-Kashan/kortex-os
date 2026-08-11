"""Local-First Template Library for the KORTEX OS Document Engine.

This module implements TemplateLibrary, which manages template schema registration,
indexing, SemVer resolution, namespace filtering, business operation searching,
and validation in accordance with Section 7 of the Document Engine Implementation Specification.
"""

from __future__ import annotations

import re
from typing import Any

from kortex.engines.document.exceptions import DocumentTemplateError
from kortex.engines.document.models import AdapterCapability, TemplateSchema

# Regular expression for SemVer 2.0.0 validation
SEMVER_REGEX = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Regular expression for namespace validation (e.g. kortex.hr.payroll or invoice.declarative.v1)
NAMESPACE_REGEX = re.compile(r"^[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)+$")

# Regular expression for placeholder identifier validation
PLACEHOLDER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


def parse_semver(version_str: str) -> tuple[int, int, int, int, str]:
    """Parse a Semantic Version string into a comparable tuple adhering to SemVer 2.0.0.

    Args:
        version_str: SemVer 2.0.0 string (e.g. '1.0.0' or '2.1.0-alpha').

    Returns:
        Tuple of (major, minor, patch, is_release, prerelease).

    Raises:
        DocumentTemplateError: If version_str is not valid SemVer format.
    """
    match = SEMVER_REGEX.match(version_str.strip())
    if not match:
        raise DocumentTemplateError(
            f"Invalid semantic version format: '{version_str}'. Must follow SemVer 2.0.0 (MAJOR.MINOR.PATCH)."
        )
    groups = match.groupdict()
    major = int(groups["major"])
    minor = int(groups["minor"])
    patch = int(groups["patch"])
    prerelease = groups["prerelease"]
    is_release = 1 if prerelease is None else 0
    return (major, minor, patch, is_release, prerelease or "")


class TemplateLibrary:
    """Local-First Template Library for storing, indexing, and validating declarative templates.

    Responsibilities:
    1. Registering and indexing TemplateSchema instances immutably by version.
    2. Validating namespace formats, SemVer, required fields, and placeholder definitions.
    3. SemVer resolution for latest and specific version lookups.
    4. Multi-criteria searching (namespace, business operation, adapter capabilities, tags, query keywords).
    5. Removal and deletion of template schemas.
    """

    def __init__(self, load_defaults: bool = True) -> None:
        """Initialize in-memory template catalog and load standard templates if enabled.

        Args:
            load_defaults: Whether to pre-load standard technology-independent templates.
        """
        # Map: template_id -> dict of (version_str -> TemplateSchema)
        self._templates: dict[str, dict[str, TemplateSchema]] = {}

        if load_defaults:
            self._load_standard_templates()

    def validate_template_schema(self, schema: TemplateSchema) -> None:
        """Validate a TemplateSchema instance prior to registration.

        Args:
            schema: TemplateSchema instance to validate.

        Raises:
            DocumentTemplateError: If any validation rule is violated.
        """
        if not schema.template_id or not schema.template_id.strip():
            raise DocumentTemplateError("Missing required field: 'template_id' cannot be empty.")

        if not schema.name or not schema.name.strip():
            raise DocumentTemplateError("Missing required field: 'name' cannot be empty.")

        if not schema.description or not schema.description.strip():
            raise DocumentTemplateError("Missing required field: 'description' cannot be empty.")

        # Validate namespace format
        if not schema.namespace or not NAMESPACE_REGEX.match(schema.namespace.strip()):
            raise DocumentTemplateError(
                f"Invalid namespace format: '{schema.namespace}'. Must be valid reverse-domain format."
            )

        # Validate SemVer format
        parse_semver(schema.version)

        # Validate placeholders
        for ph in schema.placeholders:
            if not isinstance(ph, str) or not ph.strip() or not PLACEHOLDER_REGEX.match(ph.strip()):
                raise DocumentTemplateError(
                    f"Invalid placeholder definition: '{ph}'. Must be a valid non-empty identifier."
                )

        # Validate required fields
        for req in schema.required_fields:
            if not isinstance(req, str) or not req.strip() or not PLACEHOLDER_REGEX.match(req.strip()):
                raise DocumentTemplateError(
                    f"Invalid required field definition: '{req}'. Must be a valid non-empty identifier."
                )

    async def register_template(self, schema: TemplateSchema) -> TemplateSchema:
        """Register a new TemplateSchema in the library after validation.

        Args:
            schema: TemplateSchema to register.

        Returns:
            The registered TemplateSchema instance.

        Raises:
            DocumentTemplateError: If schema validation fails or version is a duplicate.
        """
        self.validate_template_schema(schema)

        template_id = schema.template_id.strip()
        version = schema.version.strip()

        if template_id in self._templates and version in self._templates[template_id]:
            raise DocumentTemplateError(
                f"Duplicate template registration: '{template_id}' version '{version}' is already registered."
            )

        if template_id not in self._templates:
            self._templates[template_id] = {}

        self._templates[template_id][version] = schema
        return schema

    async def install_template(self, schema: TemplateSchema) -> bool:
        """Install a template into the library (ITemplateLibrary protocol method).

        Args:
            schema: TemplateSchema to install.

        Returns:
            True if installation succeeded.
        """
        await self.register_template(schema)
        return True

    async def get_template(
        self, template_id: str, version: str | None = None
    ) -> TemplateSchema:
        """Retrieve a TemplateSchema by template_id and optional version.

        If version is omitted, returns the latest version based on SemVer comparison.

        Args:
            template_id: Template identifier string.
            version: Optional SemVer string.

        Returns:
            TemplateSchema instance.

        Raises:
            DocumentTemplateError: If template_id or requested version is not found.
        """
        template_id = template_id.strip()
        if template_id not in self._templates or not self._templates[template_id]:
            raise DocumentTemplateError(f"Template '{template_id}' not found in library.")

        if version is not None:
            version = version.strip()
            if version not in self._templates[template_id]:
                raise DocumentTemplateError(
                    f"Template '{template_id}' version '{version}' not found."
                )
            return self._templates[template_id][version]

        return await self.get_latest_version(template_id)

    async def get_specific_version(
        self, template_id: str, version: str
    ) -> TemplateSchema:
        """Retrieve a specific version of a TemplateSchema.

        Args:
            template_id: Template identifier string.
            version: Specific SemVer string.

        Returns:
            TemplateSchema instance.

        Raises:
            DocumentTemplateError: If template or version is not found.
        """
        return await self.get_template(template_id, version=version)

    async def get_latest_version(self, template_id: str) -> TemplateSchema:
        """Retrieve the latest version of a TemplateSchema based on SemVer comparison.

        Args:
            template_id: Template identifier string.

        Returns:
            Latest TemplateSchema instance.

        Raises:
            DocumentTemplateError: If template_id is not found.
        """
        template_id = template_id.strip()
        if template_id not in self._templates or not self._templates[template_id]:
            raise DocumentTemplateError(f"Template '{template_id}' not found in library.")

        versions = list(self._templates[template_id].keys())
        # Sort versions using SemVer comparator
        sorted_versions = sorted(versions, key=lambda v: parse_semver(v))
        latest_ver = sorted_versions[-1]
        return self._templates[template_id][latest_ver]

    async def update_template(self, schema: TemplateSchema) -> TemplateSchema:
        """Update an existing template schema or register a new version.

        Note: Registered versions are immutable. If the exact template_id + version
        already exists, update_template rejects mutation. To update, register a new version.

        Args:
            schema: TemplateSchema instance.

        Returns:
            TemplateSchema instance.

        Raises:
            DocumentTemplateError: If attempting to overwrite an existing immutable version.
        """
        template_id = schema.template_id.strip()
        version = schema.version.strip()

        if template_id in self._templates and version in self._templates[template_id]:
            raise DocumentTemplateError(
                f"Cannot update immutable registered template '{template_id}' version '{version}'. Register a new version instead."
            )

        return await self.register_template(schema)

    async def delete_template(
        self, template_id: str, version: str | None = None
    ) -> bool:
        """Delete a template or a specific version from the library.

        Args:
            template_id: Template identifier string.
            version: Optional version string to delete. If None, deletes all versions.

        Returns:
            True if deletion succeeded.

        Raises:
            DocumentTemplateError: If template_id or specified version does not exist.
        """
        template_id = template_id.strip()
        if template_id not in self._templates or not self._templates[template_id]:
            raise DocumentTemplateError(f"Cannot delete: Template '{template_id}' not found.")

        if version is not None:
            version = version.strip()
            if version not in self._templates[template_id]:
                raise DocumentTemplateError(
                    f"Cannot delete: Template '{template_id}' version '{version}' not found."
                )
            del self._templates[template_id][version]
            if not self._templates[template_id]:
                del self._templates[template_id]
            return True

        del self._templates[template_id]
        return True

    async def remove_template(
        self, template_id: str, version: str | None = None
    ) -> bool:
        """Alias for delete_template."""
        return await self.delete_template(template_id, version=version)

    async def list_templates(
        self,
        namespace: str | None = None,
        business_operation: str | None = None,
        capability: AdapterCapability | str | None = None,
    ) -> list[TemplateSchema]:
        """List templates, optionally filtered by namespace, business operation, or capability.

        Returns the latest version for each matching template.

        Args:
            namespace: Optional namespace filter.
            business_operation: Optional business operation filter.
            capability: Optional adapter capability filter.

        Returns:
            List of matching latest TemplateSchema instances.
        """
        result: list[TemplateSchema] = []

        for template_id in self._templates:
            latest = await self.get_latest_version(template_id)

            if namespace is not None and latest.namespace.strip() != namespace.strip():
                continue

            if business_operation is not None:
                bo_schema = latest.schema_definition.get("business_operation")
                if not bo_schema or str(bo_schema).strip().lower() != business_operation.strip().lower():
                    continue

            if capability is not None:
                cap_str = capability.value if isinstance(capability, AdapterCapability) else str(capability)
                req_caps = latest.schema_definition.get("required_capabilities", [])
                if not isinstance(req_caps, list) or cap_str not in [str(c) for c in req_caps]:
                    continue

            result.append(latest)

        return result

    async def search_by_namespace(self, namespace: str) -> list[TemplateSchema]:
        """Retrieve all latest templates belonging to a specific namespace.

        Args:
            namespace: Namespace string.

        Returns:
            List of matching TemplateSchema instances.
        """
        return await self.list_templates(namespace=namespace)

    async def get_by_namespace(self, namespace: str) -> list[TemplateSchema]:
        """Alias for search_by_namespace."""
        return await self.search_by_namespace(namespace)

    async def search_by_business_operation(
        self, business_operation: str
    ) -> list[TemplateSchema]:
        """Retrieve templates supporting a specific business operation.

        Args:
            business_operation: Business operation string (e.g. 'GENERATE_PAYROLL_SLIP').

        Returns:
            List of matching TemplateSchema instances.
        """
        return await self.list_templates(business_operation=business_operation)

    async def search_by_capability(
        self, capability: AdapterCapability | str
    ) -> list[TemplateSchema]:
        """Retrieve templates requiring a specific adapter capability.

        Args:
            capability: AdapterCapability enum or string.

        Returns:
            List of matching TemplateSchema instances.
        """
        return await self.list_templates(capability=capability)

    async def search_templates(
        self, query: str, tags: list[str] | None = None
    ) -> list[TemplateSchema]:
        """Search template library by query keyword and optional tags.

        Args:
            query: Keyword query matching template_id, name, description, or namespace.
            tags: Optional list of tags to filter by.

        Returns:
            List of matching TemplateSchema instances.
        """
        query_clean = query.strip().lower()
        result: list[TemplateSchema] = []

        for template_id in self._templates:
            latest = await self.get_latest_version(template_id)
            searchable_text = f"{latest.template_id} {latest.name} {latest.description} {latest.namespace}".lower()

            if query_clean and query_clean not in searchable_text:
                continue

            if tags:
                template_tags = latest.schema_definition.get("tags", [])
                if not isinstance(template_tags, list) or not all(
                    t in template_tags for t in tags
                ):
                    continue

            result.append(latest)

        return result

    def _load_standard_templates(self) -> None:
        """Pre-load standard technology-independent declarative templates."""
        standard_templates = [
            TemplateSchema(
                template_id="invoice.declarative.v1",
                name="Standard Invoice Template",
                namespace="kortex.finance.invoice",
                version="1.0.0",
                description="Technology-independent declarative invoice template",
                placeholders=["invoice_number", "customer_name", "total_amount", "issue_date"],
                required_fields=["invoice_number", "total_amount"],
                schema_definition={
                    "business_operation": "GENERATE_INVOICE",
                    "tags": ["finance", "billing", "invoice"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="payslip.declarative.v1",
                name="Standard Payslip Template",
                namespace="kortex.hr.payroll",
                version="1.0.0",
                description="Technology-independent declarative payslip template",
                placeholders=["employee_id", "employee_name", "basic_salary", "net_salary", "period"],
                required_fields=["employee_id", "net_salary"],
                schema_definition={
                    "business_operation": "GENERATE_PAYROLL_SLIP",
                    "tags": ["hr", "payroll", "payslip"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="salary_certificate.declarative.v1",
                name="Salary Certificate Template",
                namespace="kortex.hr.payroll",
                version="1.0.0",
                description="Declarative salary certificate template",
                placeholders=["employee_name", "designation", "salary_amount", "issue_date"],
                required_fields=["employee_name", "salary_amount"],
                schema_definition={
                    "business_operation": "GENERATE_SALARY_CERTIFICATE",
                    "tags": ["hr", "certificate"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="quotation.declarative.v1",
                name="Sales Quotation Template",
                namespace="kortex.sales.quotation",
                version="1.0.0",
                description="Declarative sales quotation template",
                placeholders=["quote_number", "client_name", "valid_until", "total_price"],
                required_fields=["quote_number", "total_price"],
                schema_definition={
                    "business_operation": "GENERATE_QUOTATION",
                    "tags": ["sales", "quotation"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="purchase_order.declarative.v1",
                name="Purchase Order Template",
                namespace="kortex.procurement.po",
                version="1.0.0",
                description="Declarative purchase order template",
                placeholders=["po_number", "vendor_name", "order_date", "total_cost"],
                required_fields=["po_number", "vendor_name"],
                schema_definition={
                    "business_operation": "GENERATE_PURCHASE_ORDER",
                    "tags": ["procurement", "po"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="employment_letter.declarative.v1",
                name="Employment Letter Template",
                namespace="kortex.hr.employment",
                version="1.0.0",
                description="Declarative employment confirmation letter template",
                placeholders=["employee_name", "start_date", "job_title", "salary"],
                required_fields=["employee_name", "start_date"],
                schema_definition={
                    "business_operation": "GENERATE_EMPLOYMENT_LETTER",
                    "tags": ["hr", "employment"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="loan_letter.declarative.v1",
                name="Loan Approval Letter Template",
                namespace="kortex.hr.loan",
                version="1.0.0",
                description="Declarative employee loan approval letter template",
                placeholders=["employee_name", "loan_amount", "repayment_months"],
                required_fields=["employee_name", "loan_amount"],
                schema_definition={
                    "business_operation": "GENERATE_LOAN_LETTER",
                    "tags": ["hr", "loan"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="leave_form.declarative.v1",
                name="Leave Request Form Template",
                namespace="kortex.hr.leave",
                version="1.0.0",
                description="Declarative leave request form template",
                placeholders=["employee_name", "leave_type", "from_date", "to_date"],
                required_fields=["employee_name", "leave_type"],
                schema_definition={
                    "business_operation": "GENERATE_LEAVE_FORM",
                    "tags": ["hr", "leave"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="warning_letter.declarative.v1",
                name="Warning Letter Template",
                namespace="kortex.hr.conduct",
                version="1.0.0",
                description="Declarative warning letter template",
                placeholders=["employee_name", "incident_date", "reason"],
                required_fields=["employee_name", "reason"],
                schema_definition={
                    "business_operation": "GENERATE_WARNING_LETTER",
                    "tags": ["hr", "conduct"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="contract.declarative.v1",
                name="Standard Contract Template",
                namespace="kortex.legal.contract",
                version="1.0.0",
                description="Declarative legal contract template",
                placeholders=["party_a", "party_b", "effective_date", "terms"],
                required_fields=["party_a", "party_b"],
                schema_definition={
                    "business_operation": "GENERATE_CONTRACT",
                    "tags": ["legal", "contract"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
            TemplateSchema(
                template_id="certificate.declarative.v1",
                name="Standard Certificate Template",
                namespace="kortex.education.certificate",
                version="1.0.0",
                description="Declarative completion certificate template",
                placeholders=["recipient_name", "course_name", "issue_date"],
                required_fields=["recipient_name", "course_name"],
                schema_definition={
                    "business_operation": "GENERATE_CERTIFICATE",
                    "tags": ["certificate", "education"],
                    "required_capabilities": [AdapterCapability.GENERATE.value],
                },
            ),
        ]

        for tmpl in standard_templates:
            self._templates[tmpl.template_id] = {tmpl.version: tmpl}


__all__ = ["TemplateLibrary", "parse_semver"]
