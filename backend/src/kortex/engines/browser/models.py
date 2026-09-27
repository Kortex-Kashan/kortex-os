"""Typed contracts for the `kortex.browser.*` capability layer (Browser-B5.1).

Every model here is a *contract*, not an implementation detail — nothing in
this module references WebView2, wry, or any Tauri type. `BrowserCapabilityTarget`
is the common identity every capability shares (Section 8,
`browser_b5_architecture_gate.md`): `tenant_id`/`principal_id` are never
fields here — they come exclusively from the dispatcher-verified
`CapabilityExecutionContext` (Browser-B5 locked decision #8/#10) — only
`browser_profile_id`/`surface_id`/`navigation_generation` are per-request,
since those identify *which* browser target within that tenant/principal's
own authorized scope.
"""

from __future__ import annotations

import enum
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrowserActionErrorCode(str, enum.Enum):
    """Every typed failure a `kortex.browser.*` capability (or its later
    desktop-side redemption) can report. Never a raw COM/WebView2 exception
    string — see each capability handler's own doc for what maps to what."""

    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    POLICY_DENIED = "POLICY_DENIED"
    SURFACE_NOT_FOUND = "SURFACE_NOT_FOUND"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    STALE_REFERENCE = "STALE_REFERENCE"
    GRANT_EXPIRED = "GRANT_EXPIRED"
    GRANT_INVALID = "GRANT_INVALID"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    REFUSED_SENSITIVE_INPUT = "REFUSED_SENSITIVE_INPUT"
    NOT_YET_SUPPORTED = "NOT_YET_SUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class BrowserActionError(BaseModel):
    """Wire shape of a failed capability result. Frozen, JSON-serializable,
    never carries a raw exception message beyond what's already safe to
    return to an AI caller (no COM error text, no filesystem paths, no
    stack traces)."""

    model_config = ConfigDict(frozen=True)

    code: BrowserActionErrorCode
    message: str = Field(min_length=1)
    details: dict[str, Any] | None = None


class BrowserElementSelector(BaseModel):
    """Deterministic, accessibility-tree-derived element target — never a
    raw pixel coordinate, never a CSS/XPath selector reaching into page
    script. Mirrors the design philosophy (not the wire format) of
    `agent.proto`'s `UiElementSelector`: fails closed if every field is
    empty, and the *executor* (Browser-B5.4+, desktop-side) must fail
    closed on zero or on more than one matching element — this model only
    enforces "at least one field supplied", the uniqueness check itself is
    necessarily a runtime property this contract can't verify statically.

    `node_ref` is an opaque reference to a specific node returned by an
    immediately-preceding `browser.read`/`.extract` call (Section 7 of the
    gate) — supplying it narrows to that exact node; omitting it falls back
    to `role`/`accessible_name` matching against the live tree at execution
    time.
    """

    model_config = ConfigDict(frozen=True)

    role: str | None = None
    accessible_name: str | None = None
    node_ref: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> BrowserElementSelector:
        if not (self.role or self.accessible_name or self.node_ref):
            raise ValueError(
                "BrowserElementSelector must supply at least one of role, accessible_name, or node_ref — "
                "an all-empty selector would match every element, which this design refuses to guess between."
            )
        return self


class BrowserCapabilityTarget(BaseModel):
    """The browser-specific half of a capability's identity binding —
    `tenant_id`/`principal_id` are supplied separately, always from the
    dispatcher-verified `CapabilityExecutionContext`, never as fields here.

    `navigation_generation` is optional at the contract level (a fresh
    `browser.navigate` call has no prior generation to reference) but is
    required by the *executor* for any capability whose safety depends on
    the page not having changed since a reference was obtained (`.click`,
    `.type`, using a `node_ref` from an earlier `.read`/`.extract`) — see
    `browser_b5_architecture_gate.md` §8.
    """

    model_config = ConfigDict(frozen=True)

    browser_profile_id: str = Field(min_length=1)
    surface_id: str = Field(min_length=1)
    navigation_generation: int | None = Field(default=None, ge=0)


class BrowserNavigateParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget
    url: str = Field(min_length=1)
    timeout_ms: int = Field(default=30_000, ge=100, le=300_000)


class BrowserReadParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget


class BrowserClickParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget
    selector: BrowserElementSelector


class BrowserTypeParams(BaseModel):
    """`ui_input_text` — not `text` — deliberately: `SENSITIVE_KEY_NAMES`
    (`kortex.core.idempotency`) already redacts this exact key from every
    audit record and cached idempotency response, the same convention
    `kortex.desktop.type` already established (`desktop_automation/engine.py`)
    — reused here rather than inventing a second scrubbing mechanism."""

    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget
    selector: BrowserElementSelector
    ui_input_text: str = Field(min_length=1)


class BrowserExtractParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget
    schema_fields: dict[str, BrowserElementSelector] = Field(min_length=1)


class BrowserDownloadParams(BaseModel):
    """Registered for discovery per the B5 gate's own recommendation
    (§7/§27) — never executed. See `engine.py::download` — always raises
    `BrowserNotYetSupportedError`, unconditionally, regardless of these
    parameters' contents."""

    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget
    url: str | None = None


class BrowserScreenshotParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: BrowserCapabilityTarget
    full_page: bool = False


class BrowserCapabilityExecutionGrant(BaseModel):
    """The Capability Execution Grant (Browser-B5.3) — a short-lived, signed
    authorization artifact, NOT a general-purpose desktop transport (locked
    decision #7). Minted by a Browser capability handler after RBAC/ABAC
    (and, for a mutating capability, human approval) have already succeeded;
    redeemed exactly once by the desktop process, which independently
    re-verifies every field against live state before invoking
    `BrowserRuntime` (`browser_b5_architecture_gate.md` §6).

    `signature` is a base64-free, hex-encoded detached Ed25519 signature
    (see `grant.py::mint_grant`) over this grant's own canonical payload —
    computed via the *existing* `SecurityEngine.verification_service`
    (`Ed25519`/`ICryptoProvider`), never a new cryptographic primitive.
    `canonicalized_parameters_hash` binds the grant to the exact capability
    parameters that were authorized — never the raw parameters themselves
    (which may contain a `ui_input_text` secret-shaped value) — so this
    model is always safe to place in an audit context or log line verbatim.

    `issued_at`/`expires_at` are deliberately `str`, not `datetime` — found
    the hard way, by generating a real cross-language fixture rather than
    assuming compatibility: a `datetime` field's `.isoformat()` (what
    `grant.py`'s canonical signing payload used to call directly) produces
    `"...+00:00"`, but Pydantic's own `model_dump(mode="json")` datetime
    serialization — the actual wire format every consumer (including the
    desktop's Rust redeem command) ever sees — produces `"...Z"` instead.
    Signing one representation and shipping another would make every
    grant fail signature verification the moment it actually crossed the
    wire. Storing an already-formatted string closes this permanently:
    there is only ever one representation, used identically for signing
    and for JSON output, with no Pydantic serialization convention in the
    middle that could ever diverge from it again.
    """

    model_config = ConfigDict(frozen=True)

    grant_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    capability_name: str = Field(min_length=1)
    browser_profile_id: str = Field(min_length=1)
    surface_id: str = Field(min_length=1)
    navigation_generation: int | None = Field(default=None, ge=0)
    canonicalized_parameters_hash: str = Field(min_length=1)
    issued_at: str = Field(min_length=1, description="RFC3339 UTC timestamp, e.g. '2026-01-01T00:00:00.000000+00:00'.")
    expires_at: str = Field(min_length=1, description="RFC3339 UTC timestamp, same format as issued_at.")
    signature: str = Field(min_length=1, description="Hex-encoded detached Ed25519 signature.")


MAX_EXECUTION_REPORT_BYTES = 262_144
"""Upper bound on a serialized `execution_outcome`. Desktop-side results are
already bounded (UIA text collection, extract field set, screenshots are
reported by size only); anything larger is refused, never truncated here."""


class BrowserExecutionReport(BaseModel):
    """`kortex.browser.report_execution`'s parameters (Browser Completion
    Program, B6): the desktop's typed outcome for one claimed Grant.

    Validated for *shape* only — exactly one of `result`/`error`, each an
    object carrying the desktop wire format's own discriminator (`status`
    for a result, `kind` for an error). Whether the outcome is consistent
    with the pending execution, and whether the reporter is the principal
    that claimed it, is decided against the durable paused task record by
    the AI engine, never here and never from these fields alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    grant_id: str = Field(min_length=1, max_length=128)
    execution_outcome: dict[str, Any]

    @model_validator(mode="after")
    def _one_discriminated_outcome(self) -> BrowserExecutionReport:
        outcome = self.execution_outcome
        if len(outcome) != 1 or not ({"result", "error"} & outcome.keys()):
            raise ValueError("execution_outcome must contain exactly one of 'result' or 'error'.")
        ((key, value),) = outcome.items()
        discriminator = "status" if key == "result" else "kind"
        if not isinstance(value, dict) or not isinstance(value.get(discriminator), str):
            raise ValueError(f"execution_outcome.{key} must be an object with a string '{discriminator}'.")
        if len(json.dumps(outcome, default=str).encode("utf-8")) > MAX_EXECUTION_REPORT_BYTES:
            raise ValueError(f"execution_outcome exceeds {MAX_EXECUTION_REPORT_BYTES} bytes.")
        return self

    def audit_summary(self) -> dict[str, Any]:
        """Content-free description of the outcome — never page text,
        extracted values, or typed input."""
        ((key, value),) = self.execution_outcome.items()
        summary: dict[str, Any] = {"task_id": self.task_id, "grant_id": self.grant_id, "outcome": key}
        if key == "result":
            summary["status"] = value.get("status")
        else:
            summary["kind"] = value.get("kind")
            detail = value.get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("kind"), str):
                summary["detail_kind"] = detail["kind"]
        return summary


class BrowserGrantVerificationKey(BaseModel):
    """Response shape for `kortex.browser.grant_verification_key` — a
    PUBLIC key, not a secret; publishing it does not weaken anything (only
    the corresponding private key, held only by `BrowserCapabilityEngine`,
    must stay confidential). The desktop fetches and caches this once per
    process so grant-signature verification never has to trust "it arrived
    over an authenticated connection" alone (`browser_b5_architecture_gate.md`
    §6)."""

    model_config = ConfigDict(frozen=True)

    public_key_hex: str = Field(min_length=1)
    algorithm: str = Field(default="ed25519")


__all__ = [
    "MAX_EXECUTION_REPORT_BYTES",
    "BrowserActionError",
    "BrowserActionErrorCode",
    "BrowserCapabilityExecutionGrant",
    "BrowserCapabilityTarget",
    "BrowserClickParams",
    "BrowserDownloadParams",
    "BrowserElementSelector",
    "BrowserExecutionReport",
    "BrowserExtractParams",
    "BrowserGrantVerificationKey",
    "BrowserNavigateParams",
    "BrowserReadParams",
    "BrowserScreenshotParams",
    "BrowserTypeParams",
]
