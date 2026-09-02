"""
KORTEX Security Engine — ABAC Evaluator (Milestone M4).

Implements the ABAC half of `IAuthorizationEngine.evaluate_abac`. Pure
logic, no persistence, no I/O — `context` is caller-supplied per-call data,
never stored.

Scope decision (ratified for M4): of the four attributes named in
`docs/architecture/security_engine_implementation_spec.md` S9
(`tenant_id`, `security_classification`, `time_of_day`, `resource_ownership`),
only the first two are evaluated. `time_of_day` and `resource_ownership` are
explicit M4 non-goals: the frozen spec names them but defines zero rule
semantics for either (no time-window format, no ownership-comparison rule)
— implementing them now would mean inventing a rule format from nothing,
not deriving one from the architecture. They remain recognized-but-
unsupported attributes; nothing in this module grants access based on
either one. Deferred to a future milestone if/when the architecture defines
concrete semantics.

`tenant_id` rule (fail-closed — ratified M4 decision, ***not*** a verbatim
quote from the frozen text for the missing-value case):
`docs/architecture/multi_tenant_architecture.md` S5 states the mismatch
case explicitly — "Users with tenant_id = 'A' cannot execute capabilities on
resources belonging to tenant_id = 'B'." — but is silent on what happens
when `resource_tenant_id` is absent from `context` altogether. No frozen
document distinguishes tenant-scoped from non-tenant-scoped resources at
the `PermissionRequirement`/context level (the only adjacent frozen concept,
`shared_domain_models.md` S3's `UniversalIdentity.scope`
(`SYSTEM`/`TENANT`/`USER`/`SESSION`), describes identity scope, is not
implemented anywhere in code, and has no wiring into `PermissionRequirement`
or `context`) — so no "this resource is exempt from the tenant check" flag
is invented here. The ratified M4 decision is therefore unconditional:
a missing `resource_tenant_id` denies, exactly like a mismatched one, with
a distinct decision code so the two remain diagnosable. Every `authorize()`
call that reaches ABAC must supply a matching `resource_tenant_id` in
`context` to pass this check — there is no bypass, silent skip, or
SYSTEM/global exception.

`security_classification` rule: a clearance-vs-requirement comparison using
`PermissionRequirement.security_classification` (a real M1 model field)
against the principal's own granted clearance. The ascending ordering
`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED` implemented below (see
`_CLASSIFICATION_RANK`) is an **M4 implementation decision**, not a
verbatim frozen rule — no document states these four levels form a
hierarchy; the ordering is inferred from their being listed in this exact
sequence, consistently, across three independent frozen documents
(`shared_domain_models.md` S9, `business_entity_model.md` S15,
`document_engine_implementation_spec.md`), and from nothing in the frozen
text ever presenting them in a different order.

Principal clearance is read from `SecurityPrincipal.attributes["clearance_level"]`
— also an **M4 implementation decision**: no frozen document names this
attribute or this key; `SecurityPrincipal.attributes` is a free-form M1
ABAC attribute bag with no dedicated clearance field, and none is added
here (per the ratified decision to keep the M1 model unmodified unless an
existing authoritative field is discovered to use instead — none was). A
missing or unrecognized value defaults to the lowest rank (`PUBLIC`) — an
explicit fail-closed implementation decision: an unset clearance must never
grant anything above `PUBLIC`, and a malformed value must never be treated
as an elevated clearance.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.security.models import AccessDecision, ClassificationLevel, PermissionRequirement, SecurityPrincipal

_CLASSIFICATION_RANK = {
    ClassificationLevel.PUBLIC: 0,
    ClassificationLevel.INTERNAL: 1,
    ClassificationLevel.CONFIDENTIAL: 2,
    ClassificationLevel.RESTRICTED: 3,
}


def _classification_rank(value: object) -> int:
    """Rank a classification value, defaulting to the lowest rank (`PUBLIC`)
    for anything unrecognized — never raises, never grants an elevated rank
    for malformed input."""
    try:
        level = ClassificationLevel(value)
    except ValueError:
        return _CLASSIFICATION_RANK[ClassificationLevel.PUBLIC]
    return _CLASSIFICATION_RANK[level]


class ABACEvaluator:
    """M4 ABAC evaluator. Backs `AuthorizationEngine.evaluate_abac`. Stateless."""

    def evaluate(
        self, principal: SecurityPrincipal, requirement: PermissionRequirement, context: dict[str, Any]
    ) -> AccessDecision:
        """Evaluate `tenant_id` and `security_classification` rules.

        `context` is treated defensively — a non-dict or malformed `context`
        never crashes and never grants access; a non-dict `context`
        normalizes to `{}`, which is treated identically to an explicitly
        empty context — i.e. it fails the tenant check below (missing
        `resource_tenant_id`), never silently skips it.
        """
        safe_context: dict[str, Any] = context if isinstance(context, dict) else {}

        resource_tenant_id = safe_context.get("resource_tenant_id")
        if resource_tenant_id is None:
            return AccessDecision(
                is_allowed=False,
                decision_code="ABAC_TENANT_MISSING",
                reason="resource_tenant_id was not supplied; tenant isolation cannot be verified. Denied by default.",
            )
        if resource_tenant_id != principal.tenant_id:
            return AccessDecision(
                is_allowed=False,
                decision_code="ABAC_TENANT_MISMATCH",
                reason="Resource tenant_id does not match the principal's tenant_id.",
            )

        principal_clearance_rank = _classification_rank(principal.attributes.get("clearance_level"))
        required_rank = _classification_rank(requirement.security_classification)
        if principal_clearance_rank < required_rank:
            return AccessDecision(
                is_allowed=False,
                decision_code="ABAC_INSUFFICIENT_CLEARANCE",
                reason=(
                    f"Principal clearance ({principal_clearance_rank}) is below the required "
                    f"classification ({requirement.security_classification.value})."
                ),
            )

        return AccessDecision(
            is_allowed=True,
            decision_code="ABAC_ALLOWED",
            reason="Tenant and classification checks passed.",
        )
