"""Document Operation Profile Manager for KORTEX OS Document Engine.

This module implements DocumentOperationProfileManager, which manages discovery,
registration, validation, versioning, template resolution, and capability validation for
declarative Document Operation Profiles, in accordance with the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.exceptions import (
    AdapterNotFoundError,
    DocumentOperationError,
    DocumentProfileNotFoundError,
    DocumentTemplateError,
)
from kortex.engines.document.models import (
    AdapterPipelineDefinition,
    DocumentOperationProfile,
)
from kortex.engines.document.template_library import (
    NAMESPACE_REGEX,
    TemplateLibrary,
    parse_semver,
)


class DocumentOperationProfileManager:
    """Manager for indexing, validating, searching, and resolving DocumentOperationProfiles.

    Responsibilities:
    1. Registering and indexing DocumentOperationProfile objects immutably by version.
    2. Validating profile completeness, namespace formats, SemVer versions, and permissions.
    3. Validating required template references against TemplateLibrary if configured.
    4. Validating adapter pipeline definitions and capabilities against DocumentAdapterRegistry if configured.
    5. SemVer 2.0.0 resolution for exact and latest profile version lookups (stable vs pre-release).
    6. Business-operation and namespace filtering.
    7. Clean Architecture boundary enforcement: zero business logic, zero storage/network I/O, zero subprocesses.
    """

    def __init__(
        self,
        template_library: TemplateLibrary | None = None,
        adapter_registry: DocumentAdapterRegistry | None = None,
    ) -> None:
        """Initialize DocumentOperationProfileManager with optional dependencies.

        Args:
            template_library: Optional TemplateLibrary for validating required_template_id.
            adapter_registry: Optional DocumentAdapterRegistry for validating pipeline stage adapters.
        """
        # Map: profile_id -> dict of (version_str -> DocumentOperationProfile)
        self._profiles: dict[str, dict[str, DocumentOperationProfile]] = {}
        self._template_library = template_library
        self._adapter_registry = adapter_registry

    @property
    def template_library(self) -> TemplateLibrary | None:
        """Return configured TemplateLibrary instance."""
        return self._template_library

    @property
    def adapter_registry(self) -> DocumentAdapterRegistry | None:
        """Return configured DocumentAdapterRegistry instance."""
        return self._adapter_registry

    def validate_profile(self, profile: DocumentOperationProfile) -> None:
        """Validate a DocumentOperationProfile prior to registration.

        Args:
            profile: DocumentOperationProfile instance to validate.

        Raises:
            DocumentOperationError: If any validation constraint is violated.
        """
        if profile is None:
            raise DocumentOperationError("DocumentOperationProfile input cannot be None.")

        if not profile.id or not profile.id.strip():
            raise DocumentOperationError("Missing required field: profile 'id' cannot be empty.")

        if not profile.name or not profile.name.strip():
            raise DocumentOperationError("Missing required field: profile 'name' cannot be empty.")

        if not profile.namespace or not NAMESPACE_REGEX.match(profile.namespace.strip()):
            raise DocumentOperationError(
                f"Invalid namespace format: '{profile.namespace}'. Must be valid reverse-domain notation."
            )

        # Validate SemVer version format
        try:
            parse_semver(profile.version)
        except Exception as err:
            raise DocumentOperationError(f"Invalid profile version format: '{profile.version}'. {err}") from err

        if not profile.description or not profile.description.strip():
            raise DocumentOperationError("Missing required field: profile 'description' cannot be empty.")

        if not profile.business_operation or not profile.business_operation.strip():
            raise DocumentOperationError("Missing required field: 'business_operation' cannot be empty.")

        if not profile.output_bucket or not profile.output_bucket.strip():
            raise DocumentOperationError("Missing required field: 'output_bucket' cannot be empty.")

        # Validate permissions format
        for perm in profile.permissions:
            if not isinstance(perm, str) or not perm.strip():
                raise DocumentOperationError(f"Invalid permission entry: '{perm}'. Must be a non-empty string.")

        # Validate required template reference if TemplateLibrary is configured
        if profile.required_template_id:
            req_tmpl_id = profile.required_template_id.strip()
            if not req_tmpl_id:
                raise DocumentOperationError("Invalid required_template_id: cannot be empty string.")

            if self._template_library is not None:
                try:
                    # Sync lookup test or check presence
                    if req_tmpl_id not in self._template_library._templates:
                        raise DocumentTemplateError(
                            f"Required template '{req_tmpl_id}' specified in profile '{profile.id}' is not installed in TemplateLibrary."
                        )
                except Exception as err:
                    raise DocumentOperationError(
                        f"Template validation failed for profile '{profile.id}': {err}"
                    ) from err

        # Validate adapter pipeline definition if specified
        if profile.adapter_pipeline is not None:
            self._validate_pipeline_definition(profile.id, profile.adapter_pipeline)

    def _validate_pipeline_definition(
        self, profile_id: str, pipeline: AdapterPipelineDefinition
    ) -> None:
        """Validate pipeline definition associated with an operation profile."""
        if not pipeline.pipeline_id or not pipeline.pipeline_id.strip():
            raise DocumentOperationError(f"Pipeline definition in profile '{profile_id}' missing pipeline_id.")

        if not pipeline.stages:
            raise DocumentOperationError(f"Pipeline definition in profile '{profile_id}' contains no stages.")

        seen_stages: set[str] = set()

        for stage in pipeline.stages:
            if not stage.stage_id or not stage.stage_id.strip():
                raise DocumentOperationError(f"Stage in profile '{profile_id}' missing stage_id.")

            stage_id = stage.stage_id.strip()
            if stage_id in seen_stages:
                raise DocumentOperationError(f"Duplicate stage ID '{stage_id}' in profile '{profile_id}' pipeline.")
            seen_stages.add(stage_id)

            if not stage.adapter_id or not stage.adapter_id.strip():
                raise DocumentOperationError(
                    f"Stage '{stage_id}' in profile '{profile_id}' missing adapter_id."
                )

            # Validate adapter in registry if configured
            if self._adapter_registry is not None:
                try:
                    adapter = self._adapter_registry.get_adapter_by_id(stage.adapter_id)
                    if not adapter.supports_capability(stage.required_capability):
                        raise DocumentOperationError(
                            f"Adapter '{stage.adapter_id}' in stage '{stage_id}' does not support required capability '{stage.required_capability.value}'."
                        )
                except AdapterNotFoundError as err:
                    raise DocumentOperationError(
                        f"Adapter '{stage.adapter_id}' referenced in stage '{stage_id}' of profile '{profile_id}' is not registered."
                    ) from err

    async def register_profile(self, profile: DocumentOperationProfile) -> None:
        """Register a DocumentOperationProfile (IDocumentOperationProfileManager protocol).

        Args:
            profile: DocumentOperationProfile to register.

        Raises:
            DocumentOperationError: If validation fails or duplicate profile version exists.
        """
        self.register_profile_sync(profile)

    def register_profile_sync(self, profile: DocumentOperationProfile) -> DocumentOperationProfile:
        """Synchronous implementation of profile registration.

        Args:
            profile: DocumentOperationProfile to register.

        Returns:
            The registered DocumentOperationProfile.

        Raises:
            DocumentOperationError: If validation fails or duplicate profile version exists.
        """
        self.validate_profile(profile)

        profile_id = profile.id.strip()
        version = profile.version.strip()

        if profile_id in self._profiles and version in self._profiles[profile_id]:
            raise DocumentOperationError(
                f"Duplicate profile registration: '{profile_id}' version '{version}' is already registered."
            )

        if profile_id not in self._profiles:
            self._profiles[profile_id] = {}

        self._profiles[profile_id][version] = profile
        return profile

    async def unregister_profile(
        self, profile_id: str, version: str | None = None
    ) -> bool:
        """Unregister a profile or a specific profile version from the manager catalog.

        Args:
            profile_id: Profile identifier string.
            version: Optional SemVer string. If None, unregisters all versions.

        Returns:
            True if unregistration succeeded; False if profile_id or version was not found.
        """
        profile_id = profile_id.strip()
        if profile_id not in self._profiles or not self._profiles[profile_id]:
            return False

        if version is not None:
            version = version.strip()
            if version not in self._profiles[profile_id]:
                return False
            del self._profiles[profile_id][version]
            if not self._profiles[profile_id]:
                del self._profiles[profile_id]
            return True

        del self._profiles[profile_id]
        return True

    async def get_profile(
        self, profile_id: str, version: str | None = None
    ) -> DocumentOperationProfile:
        """Retrieve a DocumentOperationProfile by ID and optional version (IDocumentOperationProfileManager protocol).

        Args:
            profile_id: Profile identifier string.
            version: Optional SemVer string. Resolves latest version if None.

        Returns:
            DocumentOperationProfile object.

        Raises:
            DocumentProfileNotFoundError: If profile_id or version is not found.
        """
        profile_id = profile_id.strip()
        if profile_id not in self._profiles or not self._profiles[profile_id]:
            raise DocumentProfileNotFoundError(f"Document Operation Profile '{profile_id}' not found.")

        if version is not None:
            version = version.strip()
            if version not in self._profiles[profile_id]:
                raise DocumentProfileNotFoundError(
                    f"Document Operation Profile '{profile_id}' version '{version}' not found."
                )
            return self._profiles[profile_id][version]

        return await self.get_latest_version(profile_id)

    async def get_specific_version(
        self, profile_id: str, version: str
    ) -> DocumentOperationProfile:
        """Retrieve a specific version of a DocumentOperationProfile.

        Args:
            profile_id: Profile identifier string.
            version: Specific SemVer string.

        Returns:
            DocumentOperationProfile object.
        """
        return await self.get_profile(profile_id, version=version)

    async def get_latest_version(self, profile_id: str) -> DocumentOperationProfile:
        """Retrieve the latest registered version of a profile using SemVer resolution.

        Prioritizes stable versions over pre-release versions.

        Args:
            profile_id: Profile identifier string.

        Returns:
            Latest DocumentOperationProfile object.

        Raises:
            DocumentProfileNotFoundError: If profile_id is not found.
        """
        profile_id = profile_id.strip()
        if profile_id not in self._profiles or not self._profiles[profile_id]:
            raise DocumentProfileNotFoundError(f"Document Operation Profile '{profile_id}' not found.")

        versions = list(self._profiles[profile_id].keys())
        sorted_versions = sorted(versions, key=lambda v: parse_semver(v))
        latest_ver = sorted_versions[-1]
        return self._profiles[profile_id][latest_ver]

    async def list_profiles(self) -> list[DocumentOperationProfile]:
        """List latest versions of all registered DocumentOperationProfiles.

        Returns:
            List of latest DocumentOperationProfile objects.
        """
        result: list[DocumentOperationProfile] = []
        for profile_id in self._profiles:
            latest = await self.get_latest_version(profile_id)
            result.append(latest)
        return result

    async def list_all_profile_versions(self) -> list[DocumentOperationProfile]:
        """List all versions of all registered DocumentOperationProfiles.

        Returns:
            List of DocumentOperationProfile objects.
        """
        result: list[DocumentOperationProfile] = []
        for profile_id, versions_map in self._profiles.items():
            for ver_str, profile in versions_map.items():
                result.append(profile)
        return result

    async def find_by_business_operation(
        self, business_operation: str
    ) -> list[DocumentOperationProfile]:
        """Find all registered profiles matching a specific business operation code.

        Args:
            business_operation: Business operation string (e.g. 'GENERATE_PAYROLL_SLIP').

        Returns:
            List of matching latest DocumentOperationProfile objects.
        """
        bo_clean = business_operation.strip().lower()
        result: list[DocumentOperationProfile] = []

        for profile_id in self._profiles:
            latest = await self.get_latest_version(profile_id)
            if latest.business_operation.strip().lower() == bo_clean:
                result.append(latest)

        return result

    async def find_by_namespace(self, namespace: str) -> list[DocumentOperationProfile]:
        """Find all registered profiles belonging to a specific namespace.

        Args:
            namespace: Namespace string.

        Returns:
            List of matching latest DocumentOperationProfile objects.
        """
        ns_clean = namespace.strip()
        result: list[DocumentOperationProfile] = []

        for profile_id in self._profiles:
            latest = await self.get_latest_version(profile_id)
            if latest.namespace.strip() == ns_clean:
                result.append(latest)

        return result


__all__ = ["DocumentOperationProfileManager"]
