"""AI <-> Browser execution bridge: the production `IBrowserExecutionPort`.

A `kortex.browser.*` capability handler only *mints* a Capability Execution
Grant; the real action happens later, on the desktop that owns the target
surface. This module is the Browser vocabulary behind M7's
PAUSED_FOR_BROWSER_EXECUTION workflow (`agent.py`):

- recognizing a minted Grant in a tool result and validating it against the
  paused task (tenant, capability, timestamps) before anything is deferred;
- bounding how long a claimed execution may take to report
  (`report_deadline` = Grant expiry + the capability's own execution bound
  + transport grace — derived from existing desktop-side bounds, never a
  free-standing Browser timeout);
- announcing `browser.grant.pending` with identifiers only — the Grant and
  its parameters travel exclusively through the authenticated claim
  capability, never over the broadcast event relay;
- classifying a desktop-reported outcome (or an expiry) into a ToolResult
  whose `executed` field is always one of "yes"/"no"/"unknown", so an
  uncertain mutation is never presented to the model as safe to repeat.

The Grant is treated as opaque, validated data: like every other module in
this package, this one never imports another engine's internals — the
Browser engine is reached only through Kernel capability dispatch.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from kortex.engines.ai.agent import (
    AgentTask,
    BrowserGrantDeferral,
    IBrowserExecutionPort,
    PendingBrowserExecution,
)
from kortex.engines.ai.tools import (
    MAX_TOOL_OUTPUT_CHARS,
    IToolRegistry,
    ToolCall,
    ToolExecutionStatus,
    ToolResult,
)

logger = logging.getLogger("kortex.engines.ai.browser_bridge")

BROWSER_GRANT_PENDING_TOPIC: Final[str] = "browser.grant.pending"
BROWSER_EXECUTION_REPORTED_TOPIC: Final[str] = "browser.execution.reported"
BROWSER_EXECUTION_REPORTER_SENDER: Final[str] = "browser"

NAVIGATE: Final[str] = "kortex.browser.navigate"
READ: Final[str] = "kortex.browser.read"
EXTRACT: Final[str] = "kortex.browser.extract"
CLICK: Final[str] = "kortex.browser.click"
TYPE: Final[str] = "kortex.browser.type"
SCREENSHOT: Final[str] = "kortex.browser.screenshot"

DEFERRED_CAPABILITIES: Final[frozenset[str]] = frozenset({NAVIGATE, READ, EXTRACT, CLICK, TYPE, SCREENSHOT})
MUTATING_CAPABILITIES: Final[frozenset[str]] = frozenset({NAVIGATE, CLICK, TYPE})

# Mirrors of the desktop's own execution bounds (`browser_grant.rs`):
# UIA_OPERATION_MAX_TIMEOUT (15s) plus `UiaWorkerPool::submit`'s up-to-5s
# worker warm-up; SCREENSHOT_TIMEOUT (10s); navigate is bounded by its own
# `timeout_ms` parameter. `_TRANSPORT_GRACE_SECONDS` matches
# `desktop_automation/engine.py`'s identical allowance for network and
# scheduling latency, never for the UI operation itself.
_UIA_EXECUTION_BOUND_SECONDS: Final[float] = 20.0
_SCREENSHOT_EXECUTION_BOUND_SECONDS: Final[float] = 10.0
_MAX_NAVIGATE_TIMEOUT_MS: Final[int] = 300_000
_TRANSPORT_GRACE_SECONDS: Final[float] = 10.0

_MAX_EXTRACT_FIELDS: Final[int] = 100
_MAX_FIELD_VALUE_CHARS: Final[int] = 4_096

EventPublisher = Callable[..., Awaitable[object]]


class BrowserOutcomeError(ValueError):
    """A reported outcome is not a recognizable desktop execution result."""


def _parse_utc(value: object) -> datetime.datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp missing")
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed


def _execution_bound_seconds(capability_name: str, execution_parameters: dict[str, Any]) -> float:
    if capability_name == NAVIGATE:
        timeout_ms = execution_parameters.get("timeout_ms")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            timeout_ms = _MAX_NAVIGATE_TIMEOUT_MS
        return min(timeout_ms, _MAX_NAVIGATE_TIMEOUT_MS) / 1000.0
    if capability_name == SCREENSHOT:
        return _SCREENSHOT_EXECUTION_BOUND_SECONDS
    return _UIA_EXECUTION_BOUND_SECONDS


def _guidance(classification: str) -> str:
    return {
        "SECURITY_FAILURE": (
            "Refused for security reasons. Do not retry it and do not attempt a workaround; tell the user."
        ),
        "REQUIRES_REOBSERVATION": (
            "Not executed. Observe the page again (browser read) before deciding on a corrected action."
        ),
        "OUTCOME_UNKNOWN": (
            "The action may or may not have taken effect. Do NOT repeat it. Observe the page "
            "(browser read) to determine what actually happened."
        ),
        "NOT_EXECUTED": "Not executed: its authorization expired before execution began. A fresh request is required.",
        "TERMINAL": "Not executed, and it cannot succeed as requested (target unavailable or unsupported).",
        "EXECUTION_FAILED": "The action failed. Observe the page before deciding what to do next.",
        "RESOURCE_EXHAUSTED": "Not executed: the desktop was busy. It is safe to retry later.",
    }[classification]


def _failure(
    call_id: str,
    tool_name: str,
    *,
    status: ToolExecutionStatus,
    executed: str,
    classification: str,
    error_code: str,
    message: str,
    provenance: dict[str, str],
) -> ToolResult:
    # The context port renders `output` (never `error_message`) into model
    # context, so everything the model needs to act safely is in `output`.
    return ToolResult(
        call_id=call_id,
        tool_name=tool_name,
        status=status,
        output={
            "executed": executed,
            "classification": classification,
            "error_code": error_code,
            "message": message,
            "guidance": _guidance(classification),
            "provenance": provenance,
        },
        error_message=f"{error_code}: {message}",
    )


class BrowserExecutionBridgePort(IBrowserExecutionPort):
    """Production `IBrowserExecutionPort` (see module doc)."""

    def __init__(self, tool_registry: IToolRegistry, publisher: EventPublisher | None = None) -> None:
        self._tool_registry = tool_registry
        self._publisher = publisher

    def bind_publisher(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    def _capability_for(self, tool_name: str) -> str | None:
        if not self._tool_registry.has_tool(tool_name):
            return None
        return self._tool_registry.get_tool(tool_name).canonical_capability

    def is_deferred_tool(self, tool_name: str) -> bool:
        return self._capability_for(tool_name) in DEFERRED_CAPABILITIES

    def prepare_pending(
        self, task: AgentTask, tool_call: ToolCall, result: ToolResult
    ) -> BrowserGrantDeferral | ToolResult | None:
        if result.status != ToolExecutionStatus.SUCCESS:
            return None
        capability_name = self._capability_for(tool_call.tool_name) or ""
        provenance = {"source": "browser", "capability": capability_name}

        def _refuse(reason: str) -> ToolResult:
            logger.error(
                "Refusing to defer Browser call '%s' for task '%s': %s.", tool_call.call_id, task.task_id, reason
            )
            return _failure(
                result.call_id,
                result.tool_name,
                status=ToolExecutionStatus.EXECUTION_ERROR,
                executed="no",
                classification="SECURITY_FAILURE",
                error_code="GRANT_INVALID",
                message="The Browser capability did not return a valid execution Grant for this task.",
                provenance=provenance,
            )

        output = result.output
        if not isinstance(output, dict):
            return _refuse("result is not an object")
        grant = output.get("grant")
        execution_parameters = output.get("execution_parameters")
        if not isinstance(grant, dict) or not isinstance(execution_parameters, dict):
            return _refuse("grant or execution_parameters missing")
        try:
            grant_id = grant["grant_id"]
            profile_id = grant["browser_profile_id"]
            surface_id = grant["surface_id"]
            expires_at = _parse_utc(grant.get("expires_at"))
            _parse_utc(grant.get("issued_at"))
        except (KeyError, ValueError) as exc:
            return _refuse(f"malformed grant ({type(exc).__name__})")
        if not all(isinstance(v, str) and v for v in (grant_id, profile_id, surface_id)):
            return _refuse("grant identifiers missing")
        if grant.get("tenant_id") != task.tenant_id:
            return _refuse("grant tenant does not match the task tenant")
        if grant.get("capability_name") != capability_name or capability_name not in DEFERRED_CAPABILITIES:
            return _refuse("grant capability does not match the called tool")
        if execution_parameters.get("capability") != capability_name:
            return _refuse("execution parameters are for a different capability")

        deadline = expires_at + datetime.timedelta(
            seconds=_execution_bound_seconds(capability_name, execution_parameters) + _TRANSPORT_GRACE_SECONDS
        )
        placeholder = ToolResult(
            call_id=result.call_id,
            tool_name=result.tool_name,
            status=ToolExecutionStatus.SUCCESS,
            output={"browser_execution": "PENDING", "grant_id": grant_id},
            execution_time_ms=result.execution_time_ms,
        )
        return BrowserGrantDeferral(
            grant_id=grant_id,
            capability_name=capability_name,
            browser_profile_id=profile_id,
            surface_id=surface_id,
            grant_expires_at=expires_at,
            report_deadline=deadline,
            grant=grant,
            execution_parameters=execution_parameters,
            placeholder_result=placeholder,
        )

    def expired_result(self, pending: PendingBrowserExecution) -> ToolResult:
        placeholder = next(r for r in pending.pending_step.tool_results if r.call_id == pending.tool_call_id)
        provenance = {"source": "browser", "capability": pending.capability_name, "surface_id": pending.surface_id}
        if pending.claimed_by is None:
            return _failure(
                placeholder.call_id,
                placeholder.tool_name,
                status=ToolExecutionStatus.EXECUTION_ERROR,
                executed="no",
                classification="NOT_EXECUTED",
                error_code="GRANT_EXPIRED",
                message="No desktop picked up this Browser action before its authorization expired.",
                provenance=provenance,
            )
        executed = "unknown" if pending.capability_name in MUTATING_CAPABILITIES else "no"
        return _failure(
            placeholder.call_id,
            placeholder.tool_name,
            status=ToolExecutionStatus.TIMEOUT,
            executed=executed,
            classification="OUTCOME_UNKNOWN" if executed == "unknown" else "EXECUTION_FAILED",
            error_code="TIMEOUT",
            message="The desktop received this Browser action but never reported its outcome.",
            provenance=provenance,
        )

    async def notify_pending(self, task: AgentTask, pending: PendingBrowserExecution) -> None:
        if self._publisher is None:
            raise RuntimeError("No event publisher is bound; the pending Browser execution cannot be announced.")
        await self._publisher(
            topic=BROWSER_GRANT_PENDING_TOPIC,
            payload={
                "tenant_id": pending.tenant_id,
                # Relay audience: only the task owner's own event-stream
                # connections receive this notification. It carries
                # identifiers only; the Grant and its parameters are handed
                # out solely by the authenticated, owner-checked claim.
                "audience_principal_id": task.user_id,
                "task_id": task.task_id,
                "grant_id": pending.grant_id,
                "capability_name": pending.capability_name,
                "browser_profile_id": pending.browser_profile_id,
                "surface_id": pending.surface_id,
                "grant_expires_at": pending.grant_expires_at.isoformat(),
            },
            sender="ai",
        )

    def classify_outcome(self, pending: PendingBrowserExecution, outcome: object) -> ToolResult:
        """Classify a desktop-reported outcome. Raises `BrowserOutcomeError`
        for anything that is not a recognizable result/error shape — a
        malformed report is rejected, never guessed at."""
        placeholder = next(r for r in pending.pending_step.tool_results if r.call_id == pending.tool_call_id)
        call_id, tool_name = placeholder.call_id, placeholder.tool_name
        capability = pending.capability_name
        provenance = {"source": "browser", "capability": capability, "surface_id": pending.surface_id}
        mutating = capability in MUTATING_CAPABILITIES

        if not isinstance(outcome, dict) or len(outcome) != 1:
            raise BrowserOutcomeError("outcome must contain exactly one of 'result' or 'error'")
        if "result" in outcome:
            return self._classify_result(outcome["result"], call_id, tool_name, capability, provenance)
        if "error" in outcome:
            return self._classify_error(outcome["error"], call_id, tool_name, mutating, provenance)
        raise BrowserOutcomeError("outcome must contain exactly one of 'result' or 'error'")

    def _classify_result(
        self, result: object, call_id: str, tool_name: str, capability: str, provenance: dict[str, str]
    ) -> ToolResult:
        if not isinstance(result, dict):
            raise BrowserOutcomeError("result must be an object")
        status = result.get("status")
        if result.get("capability_name") != capability:
            raise BrowserOutcomeError("result capability does not match the pending execution")
        expected_status = {
            NAVIGATE: "success",
            READ: "read",
            EXTRACT: "extract",
            CLICK: "click",
            TYPE: "type",
            SCREENSHOT: "screenshot",
        }[capability]
        if status == "notYetEnabled":
            return _failure(
                call_id,
                tool_name,
                status=ToolExecutionStatus.EXECUTION_ERROR,
                executed="no",
                classification="TERMINAL",
                error_code="NOT_YET_SUPPORTED",
                message="This Browser capability is not executable on this desktop.",
                provenance=provenance,
            )
        if status != expected_status:
            raise BrowserOutcomeError("result status does not match the pending capability")

        output: dict[str, Any] = {"executed": "yes", "provenance": provenance}
        if capability == READ:
            text = result.get("text")
            truncated = result.get("truncated")
            if not isinstance(text, str) or not isinstance(truncated, bool):
                raise BrowserOutcomeError("read result requires text and truncated")
            if len(text) > MAX_TOOL_OUTPUT_CHARS:
                text, truncated = text[:MAX_TOOL_OUTPUT_CHARS], True
            output.update({"page_text": text, "truncated": truncated})
        elif capability == EXTRACT:
            fields = result.get("fields")
            if not isinstance(fields, dict) or len(fields) > _MAX_EXTRACT_FIELDS:
                raise BrowserOutcomeError("extract result requires a bounded fields object")
            output["fields"] = {str(k): self._bounded_field(v) for k, v in fields.items()}
        elif capability == SCREENSHOT:
            length = result.get("image_byte_length")
            if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                raise BrowserOutcomeError("screenshot result requires image_byte_length")
            output.update(
                {
                    "image_byte_length": length,
                    "note": "The image was captured but is not delivered into text reasoning context.",
                }
            )
        else:
            output["outcome"] = {NAVIGATE: "NAVIGATED", CLICK: "CLICKED", TYPE: "TYPED"}[capability]
        return ToolResult(call_id=call_id, tool_name=tool_name, status=ToolExecutionStatus.SUCCESS, output=output)

    @staticmethod
    def _bounded_field(value: object) -> dict[str, str | None]:
        if not isinstance(value, dict):
            raise BrowserOutcomeError("extracted field must be an object")
        bounded: dict[str, str | None] = {}
        for key in ("accessible_name", "control_type", "value"):
            item = value.get(key)
            if item is not None and not isinstance(item, str):
                raise BrowserOutcomeError("extracted field values must be strings")
            bounded[key] = item[:_MAX_FIELD_VALUE_CHARS] if isinstance(item, str) else None
        return bounded

    def _classify_error(
        self, error: object, call_id: str, tool_name: str, mutating: bool, provenance: dict[str, str]
    ) -> ToolResult:
        if not isinstance(error, dict) or not isinstance(error.get("kind"), str):
            raise BrowserOutcomeError("error must be an object with a kind")
        kind = error["kind"]
        uncertain = ("unknown", "OUTCOME_UNKNOWN") if mutating else ("no", "EXECUTION_FAILED")

        # kind -> (tool status, executed, classification, error_code, message)
        table: dict[str, tuple[ToolExecutionStatus, str, str, str, str]] = {
            "grantInvalid": (
                ToolExecutionStatus.DENIED,
                "no",
                "SECURITY_FAILURE",
                "GRANT_INVALID",
                "The desktop rejected the execution Grant.",
            ),
            "grantExpired": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "NOT_EXECUTED",
                "GRANT_EXPIRED",
                "The execution Grant expired before the desktop could execute it.",
            ),
            "surfaceNotFound": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "TERMINAL",
                "SURFACE_NOT_FOUND",
                "The target Browser surface no longer exists.",
            ),
            "profileNotFound": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "TERMINAL",
                "PROFILE_NOT_FOUND",
                "The target Browser surface is not bound to the expected profile.",
            ),
            "staleReference": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "REQUIRES_REOBSERVATION",
                "STALE_REFERENCE",
                "The page navigated since it was last observed.",
            ),
            "parametersRequired": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "TERMINAL",
                "INTERNAL_ERROR",
                "The desktop did not receive usable execution parameters.",
            ),
            "fullPageNotYetSupported": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "TERMINAL",
                "NOT_YET_SUPPORTED",
                "Full-page screenshots are not supported; only the viewport can be captured.",
            ),
            "policyDenied": (
                ToolExecutionStatus.DENIED,
                "no",
                "SECURITY_FAILURE",
                "POLICY_DENIED",
                "Browser navigation policy denied this navigation.",
            ),
            "navigationFailed": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "yes",
                "EXECUTION_FAILED",
                "NAVIGATION_FAILED",
                "The navigation started but failed to load.",
            ),
            "screenshotFailed": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "EXECUTION_FAILED",
                "INTERNAL_ERROR",
                "The screenshot could not be captured.",
            ),
            "timeout": (
                ToolExecutionStatus.TIMEOUT,
                *uncertain,
                "TIMEOUT",
                "The desktop operation did not complete within its bound.",
            ),
            "nodeRefOnlyNotSupported": (
                ToolExecutionStatus.EXECUTION_ERROR,
                "no",
                "TERMINAL",
                "NOT_YET_SUPPORTED",
                "Selectors must supply role or accessible_name; node_ref-only selection is not supported.",
            ),
            "internalError": (
                ToolExecutionStatus.EXECUTION_ERROR,
                *uncertain,
                "INTERNAL_ERROR",
                "The desktop hit an internal error while handling this action.",
            ),
        }
        if kind == "uiaFailed":
            detail = error.get("detail")
            if not isinstance(detail, dict) or not isinstance(detail.get("kind"), str):
                raise BrowserOutcomeError("uiaFailed requires a detail kind")
            entry = self._uia_entry(detail["kind"], uncertain)
        else:
            entry = table.get(
                kind,
                (
                    ToolExecutionStatus.EXECUTION_ERROR,
                    *uncertain,
                    "INTERNAL_ERROR",
                    "The desktop reported an unrecognized failure.",
                ),
            )
        status, executed, classification, error_code, message = entry
        return _failure(
            call_id,
            tool_name,
            status=status,
            executed=executed,
            classification=classification,
            error_code=error_code,
            message=message,
            provenance=provenance,
        )

    @staticmethod
    def _uia_entry(detail_kind: str, uncertain: tuple[str, str]) -> tuple[ToolExecutionStatus, str, str, str, str]:
        err = ToolExecutionStatus.EXECUTION_ERROR
        table: dict[str, tuple[ToolExecutionStatus, str, str, str, str]] = {
            "rootUnavailable": (
                err,
                "no",
                "REQUIRES_REOBSERVATION",
                "SURFACE_NOT_FOUND",
                "The surface's accessibility root was unavailable.",
            ),
            "accessibilityNotReady": (
                err,
                "no",
                "REQUIRES_REOBSERVATION",
                "TARGET_NOT_FOUND",
                "The page's accessibility tree was not ready.",
            ),
            "selectorNotFound": (
                err,
                "no",
                "REQUIRES_REOBSERVATION",
                "TARGET_NOT_FOUND",
                "No element matched the selector.",
            ),
            "selectorAmbiguous": (
                err,
                "no",
                "REQUIRES_REOBSERVATION",
                "TARGET_AMBIGUOUS",
                "More than one element matched the selector; narrow it instead of guessing.",
            ),
            "selectorUnsupported": (
                err,
                "no",
                "TERMINAL",
                "NOT_YET_SUPPORTED",
                "The selector is not supported.",
            ),
            "containmentFailed": (
                ToolExecutionStatus.DENIED,
                "no",
                "SECURITY_FAILURE",
                "FORBIDDEN",
                "The matched element is outside the target Browser surface.",
            ),
            "unsupportedControlPattern": (
                err,
                "no",
                "TERMINAL",
                "NOT_YET_SUPPORTED",
                "The matched element does not support this action.",
            ),
            "comFailure": (err, *uncertain, "INTERNAL_ERROR", "A native accessibility call failed."),
            "poolExhausted": (
                err,
                "no",
                "RESOURCE_EXHAUSTED",
                "INTERNAL_ERROR",
                "All desktop accessibility workers were busy.",
            ),
        }
        return table.get(
            detail_kind,
            (err, *uncertain, "INTERNAL_ERROR", "The desktop reported an unrecognized accessibility failure."),
        )


__all__ = [
    "BROWSER_EXECUTION_REPORTED_TOPIC",
    "BROWSER_EXECUTION_REPORTER_SENDER",
    "BROWSER_GRANT_PENDING_TOPIC",
    "DEFERRED_CAPABILITIES",
    "MUTATING_CAPABILITIES",
    "BrowserExecutionBridgePort",
    "BrowserOutcomeError",
]
