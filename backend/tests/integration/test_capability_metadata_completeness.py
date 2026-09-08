"""KORTEX OS — Automation + Integration Fabric, Milestone F1 (Capability
Model Completion): static/architectural guard proving the capability
metadata model is complete, correct, and not caller-influenceable.

Boots the real production `build_and_boot_kernel()` path (the same
convention `test_connector_api_http.py`/`test_document_api_http.py` already
use for exercising real, fully-wired production registration) rather than a
synthetic subset of engines, because the property under test —
"every capability the production boot path actually registers carries a
complete, correct risk classification" — is only true of the real boot
sequence, not of any hand-assembled subset of it.

What this guards against:

- A future engine/module capability registering with no risk
  classification at all (silently defaulting rather than failing the boot)
  — the exact condition the F1 architecture discovery identified as unsafe
  once capabilities become reachable from user-authored workflows.
- `owner_domain`/`resource_type`/`action` drifting out of sync with a
  capability's own canonical `name` (structurally impossible per
  `CapabilityDescriptor`'s computed-field design, but pinned here as a
  regression guard against that design ever being weakened).
- A future accidental change to any single engine's registration call
  silently changing that capability's risk classification — `_EXPECTED_RISK`
  below is an independent, hand-maintained copy of the intended
  classification (mirroring `test_production_capability_permissions.py`'s
  `_EXPECTED_PERMISSIONS` convention exactly), not a re-import of
  `registry/engine.py`'s own table, so a change to one without the other is
  a real, caught test failure rather than a tautology.
- Risk metadata ever becoming settable via caller-supplied
  `CapabilityRequest.parameters` rather than remaining pure registry data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.registry.engine import CapabilityDescriptor, RegistryEngine

# The intended risk classification for every capability the production boot
# path (`kernel_bootstrap.build_and_boot_kernel`) registers, as of F1.
# Kept independent of `registry/engine.py`'s own `_CAPABILITY_RISK_
# CLASSIFICATION` table on purpose — see module docstring.
_EXPECTED_RISK: dict[str, tuple[bool, bool]] = {
    "kortex.ai.agent.cancel": (False, True),
    "kortex.ai.agent.list": (True, True),
    "kortex.ai.agent.orchestrate": (False, False),
    "kortex.ai.agent.resume": (False, False),
    "kortex.ai.agent.status": (True, True),
    "kortex.ai.conversation.history.get": (True, True),
    "kortex.ai.governance.approval.create": (False, False),
    "kortex.ai.governance.audit.query": (True, True),
    "kortex.ai.governance.guardrail.check": (True, True),
    "kortex.ai.governance.policy.evaluate": (True, True),
    "kortex.ai.governance.policy.get": (True, True),
    "kortex.ai.governance.policy.upsert": (False, True),
    "kortex.ai.governance.quota.get": (True, True),
    "kortex.ai.governance.quota.update": (False, True),
    "kortex.ai.model.list": (True, True),
    "kortex.ai.provider.config.list": (True, True),
    "kortex.ai.provider.config.remove": (False, True),
    "kortex.ai.provider.configure": (False, True),
    "kortex.ai.provider.list": (True, True),
    "kortex.ai.provider.register": (False, False),
    "kortex.ai.provider.test": (True, True),
    "kortex.ai.response.generate": (False, False),
    "kortex.ai.tool.invoke": (False, False),
    "kortex.backup.create": (False, False),
    "kortex.backup.delete": (False, False),
    "kortex.backup.diagnostics.get": (True, True),
    "kortex.backup.get": (True, True),
    "kortex.backup.list": (True, True),
    "kortex.backup.verify": (True, True),
    "kortex.connector.action.execute": (False, False),
    "kortex.connector.driver.list": (True, True),
    "kortex.connector.driver.register": (False, False),
    "kortex.connector.notification.webhook.send": (False, False),
    "kortex.connector.notification.webhook.status": (True, True),
    "kortex.connector.profile.delete": (False, False),
    "kortex.connector.profile.get": (True, True),
    "kortex.connector.profile.list": (True, True),
    "kortex.connector.profile.register": (False, True),
    "kortex.document.adapter.list": (True, True),
    "kortex.document.adapter.register": (False, False),
    "kortex.document.intelligence.analyze": (True, True),
    "kortex.document.lifecycle.transition": (False, False),
    "kortex.document.operation.execute": (False, False),
    "kortex.document.preview.generate": (True, True),
    "kortex.document.profile.list": (True, True),
    "kortex.document.recommendation.get": (True, True),
    "kortex.document.template.bind": (True, True),
    "kortex.document.template.list": (True, True),
    "kortex.document_intelligence.ocr.extract": (True, True),
    "kortex.document_intelligence.pdf.parse": (True, True),
    "kortex.document_intelligence.structure.analyze": (True, True),
    "kortex.finance.invoice.create": (False, False),
    "kortex.finance.invoice.get": (True, True),
    "kortex.hr_payroll.attendance.check_in": (False, False),
    "kortex.hr_payroll.attendance.check_out": (False, False),
    "kortex.hr_payroll.attendance.list": (True, True),
    "kortex.hr_payroll.employee.create": (False, False),
    "kortex.hr_payroll.employee.get": (True, True),
    "kortex.hr_payroll.employee.list": (True, True),
    "kortex.hr_payroll.leave.balance_get": (True, True),
    "kortex.hr_payroll.leave.decide": (False, False),
    "kortex.hr_payroll.leave.request": (False, False),
    "kortex.hr_payroll.payroll.calculate": (False, False),
    "kortex.hr_payroll.payroll.run_get": (True, True),
    "kortex.hr_payroll.payslip.get": (True, True),
    "kortex.knowledge.graph.list": (True, True),
    "kortex.knowledge.graph.traverse": (True, True),
    "kortex.knowledge.pack.load": (False, False),
    "kortex.knowledge.query.search": (True, True),
    "kortex.knowledge.source.index": (False, False),
    "kortex.license.activation.apply": (False, True),
    "kortex.license.activation.revoke": (False, True),
    "kortex.license.status.get": (True, True),
    "kortex.license.token.verify": (True, True),
    "kortex.marketplace.listing.list": (True, True),
    "kortex.monitoring.dashboard.get": (True, True),
    "kortex.monitoring.diagnostics.get": (True, True),
    "kortex.monitoring.metrics.get": (True, True),
    "kortex.monitoring.timeseries.get": (True, True),
    "kortex.operations.incident.close": (False, False),
    "kortex.operations.incident.get": (True, True),
    "kortex.operations.incident.list": (True, True),
    "kortex.operations.incident.report": (False, False),
    "kortex.operations.incident.resolve": (False, False),
    "kortex.operations.incident.status_update": (False, False),
    "kortex.operations.vehicle.assign": (False, False),
    "kortex.operations.vehicle.create": (False, False),
    "kortex.operations.vehicle.get": (True, True),
    "kortex.operations.vehicle.list": (True, True),
    "kortex.operations.vehicle.status_update": (False, False),
    "kortex.operations.vehicle.tracking_history": (True, True),
    "kortex.operations.vehicle.tracking_record": (False, False),
    "kortex.operations.vehicle.unassign": (False, False),
    "kortex.security.access.authorize": (True, True),
    "kortex.security.auth.authenticate": (False, False),
    "kortex.security.auth.change_password": (False, True),
    "kortex.security.auth.request_password_reset": (False, False),
    "kortex.security.auth.reset_password": (False, False),
    "kortex.security.bootstrap.create_admin": (False, False),
    "kortex.security.oauth.get_config": (True, True),
    "kortex.security.oauth.link_begin": (False, False),
    "kortex.security.oauth.link_complete": (False, False),
    "kortex.security.oauth.list_links": (True, True),
    "kortex.security.oauth.login_begin": (False, False),
    "kortex.security.oauth.login_complete": (False, False),
    "kortex.security.oauth.unlink": (False, True),
    "kortex.security.principal.register": (False, False),
    "kortex.security.principal.set_email": (False, True),
    "kortex.security.secret.get": (True, True),
    "kortex.security.secret.put": (False, True),
    "kortex.security.signature.verify": (True, True),
    "kortex.sentinel.diagnostics.get": (True, True),
    "kortex.sentinel.health.get": (True, True),
    "kortex.sentinel.status.get": (True, True),
    "kortex.storage.cache.set": (False, True),
    "kortex.storage.data.session": (True, True),
    "kortex.storage.file.store": (False, True),
    "kortex.storage.object.put": (False, True),
    "kortex.workflow.approval.create": (False, False),
    "kortex.workflow.approval.decide": (False, False),
    "kortex.workflow.approval.delegate": (False, False),
    "kortex.workflow.approval.get": (True, True),
    "kortex.workflow.approval.list": (True, True),
    "kortex.workflow.definition.archive": (False, True),
    "kortex.workflow.definition.clone": (False, False),
    "kortex.workflow.definition.create": (False, False),
    "kortex.workflow.definition.get": (True, True),
    "kortex.workflow.definition.list": (True, True),
    "kortex.workflow.definition.publish": (False, False),
    "kortex.workflow.definition.update": (False, True),
    "kortex.workflow.definition.validate": (True, True),
    "kortex.workflow.external.cancel": (False, False),
    "kortex.workflow.external.execute": (False, False),
    "kortex.workflow.external.get": (True, True),
    "kortex.workflow.external.list": (True, True),
    "kortex.workflow.instance.approve": (False, False),
    "kortex.workflow.instance.cancel": (False, False),
    "kortex.workflow.instance.get": (True, True),
    "kortex.workflow.instance.list": (True, True),
    "kortex.workflow.instance.resume": (False, False),
    "kortex.workflow.instance.start": (False, False),
    "kortex.workflow.schedule.cancel": (False, True),
    "kortex.workflow.schedule.create": (False, False),
    "kortex.workflow.schedule.get": (True, True),
    "kortex.workflow.schedule.list": (True, True),
    "kortex.workflow.schedule.pause": (False, True),
    "kortex.workflow.schedule.resume": (False, True),
    "kortex.workflow.schedule.trigger": (False, False),
    "kortex.workflow.state.get": (True, True),
}

_MASTER_KEY = "0x" + ("aa" * 32)
_SIGNING_KEY = "0x" + ("bb" * 32)


@pytest_asyncio.fixture
async def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "capmeta_storage"))
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'capmeta.db').as_posix()}")
    monkeypatch.setenv("KORTEX_MASTER_KEY", _MASTER_KEY)
    monkeypatch.setenv("KORTEX_AUTH_SIGNING_PRIVATE_KEY", _SIGNING_KEY)

    from kortex.api.kernel_bootstrap import build_and_boot_kernel

    booted = await build_and_boot_kernel()
    try:
        yield booted
    finally:
        await booted.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_every_production_capability_has_complete_derived_metadata(kernel: Kernel) -> None:
    """Every capability the real production boot path registers must resolve a non-empty
    owner_domain/resource_type/action — proving `_capability_name_segments` never silently
    produces a blank/nonsensical decomposition for any capability actually shipped."""
    caps = kernel.list_capabilities()
    assert len(caps) > 0

    incomplete = [
        c.name for c in caps if not c.owner_domain.strip() or not c.resource_type.strip() or not c.action.strip()
    ]
    assert incomplete == [], f"Capabilities with incomplete derived metadata: {incomplete}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_production_capability_risk_classification_matches_expected(kernel: Kernel) -> None:
    """Every capability the real production boot path registers must carry exactly the risk
    classification `_EXPECTED_RISK` declares — an independent, hand-maintained expectation (not a
    re-import of the production classification table), so a future accidental change to either is
    caught as a real failure rather than silently drifting past both."""
    caps = {c.name: c for c in kernel.list_capabilities()}

    missing_from_expected = sorted(set(caps) - set(_EXPECTED_RISK))
    missing_from_production = sorted(set(_EXPECTED_RISK) - set(caps))
    assert missing_from_expected == [], (
        f"Production registered capabilities with no entry in this test's _EXPECTED_RISK table "
        f"(update the table, or investigate why an unclassified capability boots at all): "
        f"{missing_from_expected}"
    )
    assert missing_from_production == [], (
        f"_EXPECTED_RISK entries for capabilities no longer registered by production boot "
        f"(stale test expectations): {missing_from_production}"
    )

    mismatches = []
    for name, (expected_read_only, expected_idempotent) in _EXPECTED_RISK.items():
        descriptor = caps[name]
        if descriptor.is_read_only != expected_read_only or descriptor.is_idempotent != expected_idempotent:
            mismatches.append(
                f"{name}: expected (is_read_only={expected_read_only}, is_idempotent={expected_idempotent}), "
                f"got (is_read_only={descriptor.is_read_only}, is_idempotent={descriptor.is_idempotent})"
            )
    assert mismatches == [], "Risk classification drift:\n" + "\n".join(mismatches)


def test_unclassified_capability_registers_with_fail_closed_default() -> None:
    """A capability registering with no explicit is_read_only/is_idempotent and no entry in the
    production risk-classification table must still register successfully — F1 must not break any
    existing capability registration (production or test) over missing classification alone — but
    lands on the same fail-closed default (mutating, non-idempotent) `CapabilityDescriptor` itself
    declares, never a silently permissive one."""
    registry = RegistryEngine()
    descriptor = registry.register_capability(
        name="kortex.f1_test.unclassified.probe",
        description="F1 guard probe — unclassified capability.",
        provider="test",
    )
    assert descriptor.is_read_only is False
    assert descriptor.is_idempotent is False


def test_explicit_risk_classification_satisfies_registration_without_a_table_entry() -> None:
    """A brand-new capability (absent from the production table) that explicitly declares its own
    is_read_only/is_idempotent registers successfully — proving F1's intended path forward for
    every future capability author is to classify at the call site, not to grow the transitional
    table."""
    registry = RegistryEngine()
    descriptor = registry.register_capability(
        name="kortex.f1_test.explicit.probe",
        description="F1 guard probe — explicit classification.",
        provider="test",
        is_read_only=True,
        is_idempotent=True,
    )
    assert descriptor.is_read_only is True
    assert descriptor.is_idempotent is True
    assert descriptor.owner_domain == "f1_test"
    assert descriptor.resource_type == "explicit"
    assert descriptor.action == "probe"


def test_non_canonical_capability_name_degrades_gracefully_without_breaking_registration() -> None:
    """A capability name that doesn't follow the canonical `kortex.<domain>.<resource>.<action>`
    convention (`capability_registry.md` §1) — e.g. the synthetic, unprefixed names many pre-F1
    unit tests already register, such as `"dispatch.test.allowed"` — must keep registering exactly
    as it did before F1. `owner_domain`/`resource_type`/`action` degrade to a best-effort,
    non-fabricated decomposition of whatever segments the name actually has, rather than raising:
    F1 must not break a single existing capability registration over name shape alone."""
    registry = RegistryEngine()
    descriptor = registry.register_capability(
        name="dispatch.test.allowed",
        description="F1 guard probe — non-canonical name.",
        provider="test",
        is_read_only=True,
        is_idempotent=True,
    )
    assert descriptor.owner_domain == "dispatch"
    assert descriptor.resource_type == "test"
    assert descriptor.action == "allowed"

    single_segment = registry.register_capability(
        name="probe",
        description="F1 guard probe — single-segment name.",
        provider="test",
        is_read_only=True,
        is_idempotent=True,
    )
    assert single_segment.owner_domain == "probe"
    assert single_segment.resource_type == "probe"
    assert single_segment.action == "probe"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_risk_metadata_is_not_influenceable_by_caller_supplied_parameters(kernel: Kernel) -> None:
    """A caller cannot change a capability's registry-authoritative risk classification by smuggling
    `is_read_only`/`is_idempotent` keys into `CapabilityRequest.parameters` — dispatch never reads
    classification off request content, only off the registry descriptor built at registration time.

    Uses `kortex.security.oauth.get_config` because it is bootstrap-exempt
    (`requires_authentication=False`) and its handler accepts arbitrary `**_extra` kwargs, so this
    proves the invariant through a real `Kernel.invoke_capability()` dispatch — not merely by
    inspecting `CapabilityRequest`'s schema — without needing to seed a principal/session first.
    """
    target = "kortex.security.oauth.get_config"
    before = kernel.get_capability(target)
    assert before.is_read_only is True
    assert before.is_idempotent is True

    request = CapabilityRequest(
        capability_name=target,
        parameters={
            "is_read_only": False,
            "is_idempotent": False,
            "owner_domain": "attacker-controlled",
            "resource_type": "attacker-controlled",
            "action": "attacker-controlled",
        },
    )
    result = await kernel.invoke_capability(request)
    assert isinstance(result, dict)  # the real handler's own return shape, unaffected

    after = kernel.get_capability(target)
    assert after.is_read_only is True
    assert after.is_idempotent is True
    assert after.owner_domain == "security"
    assert after.resource_type == "oauth"
    assert after.action == "get_config"


def test_capability_descriptor_risk_fields_default_fail_closed() -> None:
    """A directly-constructed `CapabilityDescriptor` (bypassing `register_capability` entirely,
    e.g. in a hand-rolled test fixture elsewhere) defaults `is_read_only`/`is_idempotent` to False
    — mutating/non-idempotent until proven otherwise — never to a permissive True."""
    descriptor = CapabilityDescriptor(
        name="kortex.f1_test.default.probe",
        description="F1 guard probe — construction defaults.",
        provider="test",
    )
    assert descriptor.is_read_only is False
    assert descriptor.is_idempotent is False


def test_search_capabilities_filters_are_purely_additive() -> None:
    """`RegistryEngine.search_capabilities` combines every supplied filter with AND semantics and
    never mutates the underlying catalog — a regression guard for the F1 discovery/catalog surface
    every future consumer (node library, AI tool generator, integration catalog) will rely on."""
    registry = RegistryEngine()
    registry.register_capability(
        name="kortex.f1_test.widget.list",
        description="list widgets",
        provider="test",
        is_read_only=True,
        is_idempotent=True,
    )
    registry.register_capability(
        name="kortex.f1_test.widget.delete",
        description="delete a widget",
        provider="test",
        is_read_only=False,
        is_idempotent=False,
    )
    registry.register_capability(
        name="kortex.f1_test.gadget.list",
        description="list gadgets",
        provider="test",
        is_read_only=True,
        is_idempotent=True,
    )

    assert {c.name for c in registry.search_capabilities(owner_domain="f1_test")} == {
        "kortex.f1_test.widget.list",
        "kortex.f1_test.widget.delete",
        "kortex.f1_test.gadget.list",
    }
    assert {c.name for c in registry.search_capabilities(resource_type="widget")} == {
        "kortex.f1_test.widget.list",
        "kortex.f1_test.widget.delete",
    }
    assert {c.name for c in registry.search_capabilities(owner_domain="f1_test", is_read_only=True)} == {
        "kortex.f1_test.widget.list",
        "kortex.f1_test.gadget.list",
    }
    assert {c.name for c in registry.search_capabilities(owner_domain="f1_test", action="delete")} == {
        "kortex.f1_test.widget.delete",
    }
    assert {c.name for c in registry.search_capabilities(owner_domain="f1_test", keyword="gadget")} == {
        "kortex.f1_test.gadget.list",
    }
    assert registry.search_capabilities(owner_domain="does-not-exist") == []
