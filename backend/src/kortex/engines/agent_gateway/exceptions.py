"""Exception hierarchy for the Phase 6 desktop-command transport.

These describe failures in *routing* a command to a live, authenticated
agent session and correlating its response — never failures of what the
command asked the agent to do on the Windows side (those come back as a
successful RPC carrying `DesktopCommandResult.success=False`, decoded by
`kortex.engines.desktop_automation`). Keeping the two separate means a
transport failure (no agent connected, ambiguous agent, timeout) is never
mistaken for — or silently downgraded into — a UI-level outcome.
"""

from __future__ import annotations


class DesktopCommandTransportError(Exception):
    """Base exception for every desktop-command transport failure."""


class DesktopAgentUnavailableError(DesktopCommandTransportError):
    """No authenticated agent session exists for the requested tenant/agent.

    Fail-closed: zero matching sessions is treated identically whether no
    agent has ever connected or the one that had has since disconnected.
    """


class DesktopAgentAmbiguousError(DesktopCommandTransportError):
    """More than one live session matches and no `agent_id` disambiguated it.

    Mirrors the UI-element-selector rule (zero matches -> fail, one match ->
    proceed, more than one -> fail closed) one level up: at agent selection,
    not just at element selection. A command is never routed to an arbitrarily
    chosen agent among several live candidates.
    """


class DesktopCommandTimeoutError(DesktopCommandTransportError):
    """The agent did not return a correlated result within the deadline."""


class DesktopSessionDisconnectedError(DesktopCommandTransportError):
    """The owning session closed (revoked, disconnected) while the command
    was still in flight. Distinct from a timeout: the session is known to be
    gone, not merely slow."""


__all__ = [
    "DesktopAgentAmbiguousError",
    "DesktopAgentUnavailableError",
    "DesktopCommandTimeoutError",
    "DesktopCommandTransportError",
    "DesktopSessionDisconnectedError",
]
