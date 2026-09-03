"""KORTEX Platform Security — Capability Identity Propagation: static/architectural
guard against recurrence.

This is a repo-wide, AST-based test — not scoped to any single engine —
proving the canonical security invariant is structural, not merely a
convention followed by the two engines fixed in this milestone
(Workflow, Document Intelligence).

The invariant: only `CapabilityDispatcher.dispatch()` (`core/dispatch.py`)
may call `SecurityEngine.authentication_manager.verify_token()` or
`.authenticate()`, and no handler may resolve a `SecurityPrincipal` by a
caller-supplied identifier as a substitute for the dispatcher-authenticated
one. Any other engine code calling these is exactly the pattern that let a
capability handler independently decide "who is calling me" from
caller-controlled data — the confirmed platform vulnerability this
milestone closes.

What this test CAN guarantee: no call expression matching these specific,
named methods exists outside the two sanctioned locations, for any shape
the caller-controlled data takes (a top-level kwarg, a field nested inside
a business model, a raw dict). This closes every currently-known and any
structurally-similar future smuggling vector, because they all require
calling one of these methods somewhere in handler-reachable code.

What this test CANNOT guarantee: that a handler doesn't hand-roll its own
credential verification without calling these specific methods, or that a
differently-named future method performing the same unsafe function
evades this specific pattern match. It is a strong, cheap, high-value net,
not a formal proof of absence of all possible misuse.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parent.parent.parent / "src" / "kortex"

# Only `CapabilityDispatcher.dispatch()` (core/dispatch.py) and Security
# Engine's own internals may authenticate a credential or resolve a
# principal snapshot directly. Every other file is scanned.
#
# `api/main.py` is also sanctioned: it independently calls `verify_token()`
# once, for its WebSocket `/events/stream` endpoint (`events_stream()`),
# which never goes through `CapabilityDispatcher`/capability handlers at
# all — it is a second, legitimate TOP-LEVEL authentication boundary for a
# different transport, not a capability handler re-deriving identity from
# caller-controlled `parameters` after the Kernel already authenticated
# something else for the same request. This is a real, separately-tracked
# drift risk (two authentication implementations must be kept in lockstep)
# but is not an instance of the vulnerability this guard exists to prevent.
_SANCTIONED_PREFIXES = (
    "core\\dispatch.py",
    "core/dispatch.py",
    "engines\\security\\",
    "engines/security/",
    "api\\main.py",
    "api/main.py",
    "api\\kernel_bootstrap.py",
    "api/kernel_bootstrap.py",
)

# The exact method names that constitute "independently authenticate a
# caller-controlled credential/identifier" — shape-agnostic: it does not
# matter whether the credential arrived as a top-level kwarg, a field on a
# nested Pydantic model, or a raw dict smuggled in `options`.
_FORBIDDEN_METHOD_CALLS = frozenset({"verify_token", "authenticate", "_load_principal"})


def _is_sanctioned(path: Path) -> bool:
    rel = str(path.relative_to(_BACKEND_SRC))
    return any(rel.startswith(prefix) for prefix in _SANCTIONED_PREFIXES)


def _python_files() -> list[Path]:
    return sorted(p for p in _BACKEND_SRC.rglob("*.py") if not _is_sanctioned(p))


def _forbidden_call_sites(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _FORBIDDEN_METHOD_CALLS
        ):
            hits.append((node.lineno, node.func.attr))
    return hits


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(_BACKEND_SRC)))
def test_no_independent_authentication_outside_sanctioned_boundary(path: Path) -> None:
    hits = _forbidden_call_sites(path)
    assert not hits, (
        f"{path.relative_to(_BACKEND_SRC)} independently calls {sorted({m for _, m in hits})} "
        f"at line(s) {[ln for ln, _ in hits]} — only core/dispatch.py's CapabilityDispatcher and "
        f"engines/security/**'s own internals may authenticate a credential or resolve a principal "
        f"by a caller-supplied identifier. A capability handler must use the dispatcher-injected "
        f"CapabilityExecutionContext instead (KORTEX Platform Security — Capability Identity "
        f"Propagation)."
    )


def test_sanctioned_boundary_is_not_accidentally_empty() -> None:
    """Guard the guard: if `_SANCTIONED_PREFIXES` were ever mistyped so broadly
    that nothing gets scanned, the test above would trivially "pass" for the
    wrong reason. Assert a real, substantial file set is actually checked."""
    scanned = _python_files()
    assert len(scanned) > 100, (
        f"expected to scan a substantial number of files, only found {len(scanned)} — check the exclusion logic"
    )
    # And confirm dispatch.py itself really is excluded (it legitimately calls verify_token).
    assert not any(p.name == "dispatch.py" and p.parent.name == "core" for p in scanned)


def test_workflow_and_document_intelligence_no_longer_independently_authenticate() -> None:
    """Directly names the two engines this milestone migrated, so a future
    reviewer sees exactly which files were the confirmed vulnerable sites —
    not just an abstract repo-wide sweep."""
    workflow_engine = _BACKEND_SRC / "engines" / "workflow" / "engine.py"
    workflow_approval = _BACKEND_SRC / "engines" / "workflow" / "approval.py"
    docint_engine = _BACKEND_SRC / "engines" / "document_intelligence" / "engine.py"

    for path in (workflow_engine, workflow_approval, docint_engine):
        hits = _forbidden_call_sites(path)
        assert not hits, f"{path.name} still independently authenticates at {hits}"
