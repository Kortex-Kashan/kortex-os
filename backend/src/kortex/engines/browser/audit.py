"""Shared Browser capability audit-event vocabulary (Browser-B5.0/B5.4).

Two audit layers exist for Browser, deliberately kept distinct (locked
decision, `docs/architecture/browser_decision_log.md` D-series): the
**backend** `AuditManager`/`UniversalAuditEntry` (this module), first
reachable for Browser at all as of B5 since B5 is the first phase where a
Browser action is dispatched through `CapabilityDispatcher`; and B4's own
**local**, JSON-Lines `<profiles_root>/audit.log` (`browser_policy.rs`,
unchanged), which continues to record the WebView2-COM-level enforcement
facts it always has, entirely independent of whether a backend capability
was ever involved (a human click never touches the backend at all).

This module defines all six event names the B5 foundation establishes.
Only `BROWSER_GRANT_MINTED` is actually recorded through the backend
`AuditManager` by anything in this phase (`engine.py`'s handlers, which are
the only backend-side code that runs in B5.0-B5.4) — `BROWSER_GRANT_REDEEMED`,
`BROWSER_GRANT_REJECTED`, `BROWSER_EXECUTION_STARTED`,
`BROWSER_EXECUTION_SUCCEEDED`, and `BROWSER_EXECUTION_FAILED` describe
events that occur at Grant *redemption* time — desktop-side, inside the new
Rust redeem command (Browser-B5.4) — and are recorded there, into the
local `audit.log`, using these exact same string names for vocabulary
consistency between the two logs. Routing those five into the backend
`AuditManager` too requires a capability call the desktop can make to
report its outcome back (`kortex.browser.report_execution` or equivalent)
— explicitly out of scope for B5.0-B5.4 (see `browser_b5_architecture_gate.md`
§29 Q1 and the B5.0-B5.4 authorization's own "DO NOT implement
browser.navigate execution" instruction) and left to B5.5+.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kortex.engines.security.audit import AuditManager

BROWSER_GRANT_MINTED = "BROWSER_GRANT_MINTED"
BROWSER_GRANT_REDEEMED = "BROWSER_GRANT_REDEEMED"
BROWSER_GRANT_REJECTED = "BROWSER_GRANT_REJECTED"
BROWSER_EXECUTION_STARTED = "BROWSER_EXECUTION_STARTED"
BROWSER_EXECUTION_SUCCEEDED = "BROWSER_EXECUTION_SUCCEEDED"
BROWSER_EXECUTION_FAILED = "BROWSER_EXECUTION_FAILED"

# Recorded by the backend AuditManager as of B5.0-B5.4 (the only phase of
# these six that runs backend-side today).
BACKEND_RECORDED_EVENTS = frozenset({BROWSER_GRANT_MINTED})
# Recorded by the desktop's own local audit.log (Browser-B5.4, Rust side).
DESKTOP_LOCAL_RECORDED_EVENTS = frozenset(
    {
        BROWSER_GRANT_REDEEMED,
        BROWSER_GRANT_REJECTED,
        BROWSER_EXECUTION_STARTED,
        BROWSER_EXECUTION_SUCCEEDED,
        BROWSER_EXECUTION_FAILED,
    }
)


async def record_browser_audit_event(
    audit_manager: AuditManager,
    event: str,
    *,
    tenant_id: str,
    actor_id: str,
    resource_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Record one Browser capability audit event through the backend
    `AuditManager`, using `record_event`'s own existing convention exactly
    (`kortex.engines.security.audit.AuditManager.record_event`) — no new
    persistence path, no new event shape.

    `context` must never contain a secret-shaped value (a typed
    `ui_input_text`, a raw screenshot, unsanitized page content, a session
    token) — callers are responsible for passing only already-safe data
    (e.g. a `canonicalized_parameters_hash`, never raw parameters). This
    function performs no redaction of its own beyond what `AuditManager`/
    `UniversalAuditEntry` already do, matching every other capability's
    audit call site in this codebase (none of them redact at the call
    site either — the discipline is "never pass a secret in", not "trust
    a filter to catch it on the way out").

    Best-effort, matching `CapabilityDispatcher`'s own audit posture
    (`dispatch.py`'s `_audit_*` methods): an audit-store outage must never
    fail or block the capability action it describes, so any exception
    from `record_event` itself is logged and swallowed here, never
    propagated to the caller.
    """
    try:
        await audit_manager.record_event(
            action=event,
            actor_id=actor_id,
            actor_type="AI_AGENT",
            tenant_id=tenant_id,
            resource_id=resource_id,
            context=context or {},
        )
    except Exception:
        logging.getLogger("kortex.engine.browser").warning(
            "Failed to record Browser audit event '%s' for tenant '%s'.", event, tenant_id, exc_info=True
        )


__all__ = [
    "BACKEND_RECORDED_EVENTS",
    "BROWSER_EXECUTION_FAILED",
    "BROWSER_EXECUTION_STARTED",
    "BROWSER_EXECUTION_SUCCEEDED",
    "BROWSER_GRANT_MINTED",
    "BROWSER_GRANT_REDEEMED",
    "BROWSER_GRANT_REJECTED",
    "DESKTOP_LOCAL_RECORDED_EVENTS",
    "record_browser_audit_event",
]
