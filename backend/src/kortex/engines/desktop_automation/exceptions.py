"""Exception hierarchy for the Desktop Automation capability layer.

Distinct from `kortex.engines.agent_gateway.exceptions`: those describe a
failure to *route* a command to a live agent session at all (no agent
connected, ambiguous agent, timeout). These describe the Desktop Agent
having received the command and reported that the requested UI-level
operation itself could not be completed — decoded from a successful RPC
carrying `DesktopCommandResult.success=False` and its `error_code`.
"""

from __future__ import annotations


class DesktopAutomationError(Exception):
    """Base exception for every capability-level desktop automation failure."""

    def __init__(self, message: str, error_code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class DesktopApplicationNotAllowedError(DesktopAutomationError):
    """`application_id` is not present in the agent's local allow-list.

    Raised for an unrecognized key just as much as for one that resolves to
    an executable the operator never approved — the two are indistinguishable
    to the caller by design, so a probing caller cannot use the error to
    enumerate which application ids exist.
    """


class DesktopLaunchFailedError(DesktopAutomationError):
    """The allow-listed application failed to start or never produced a
    usable main window within its timeout."""


class DesktopWindowNotFoundError(DesktopAutomationError):
    """`window_handle` does not refer to a window this agent is still
    tracking — never issued, already exited, or already evicted for being
    stale."""


class DesktopElementNotFoundError(DesktopAutomationError):
    """The selector matched zero UI elements. The agent never guesses; a
    caller must narrow the selector or verify the window's actual state."""


class DesktopElementAmbiguousError(DesktopAutomationError):
    """The selector matched more than one UI element. The agent never picks
    one arbitrarily; a caller must supply a selector that resolves uniquely."""


class DesktopCommandFailedError(DesktopAutomationError):
    """The agent reported failure with an error_code this client does not
    recognize, or one otherwise not mapped to a more specific exception.
    Still a decoded, structured failure — never a raw transport error."""


class DesktopInvalidSelectorError(DesktopAutomationError):
    """A selector with every field empty was supplied.

    Rejected before the command is ever sent: an all-empty selector would
    match every element in the window, which is the ambiguous case this
    entire design exists to refuse — there is no reason to pay a network
    round trip to learn that.
    """


# Wire vocabulary shared with the Desktop Agent's `DesktopCommandResult.error_code`.
# Both sides must agree on these literal strings; see `apps/desktop-agent`'s
# `DesktopAutomationHandler` for the producing side.
_ERROR_CODE_MAP: dict[str, type[DesktopAutomationError]] = {
    "APPLICATION_NOT_ALLOWED": DesktopApplicationNotAllowedError,
    "LAUNCH_FAILED": DesktopLaunchFailedError,
    "WINDOW_NOT_FOUND": DesktopWindowNotFoundError,
    "ELEMENT_NOT_FOUND": DesktopElementNotFoundError,
    "ELEMENT_AMBIGUOUS": DesktopElementAmbiguousError,
}


def error_for_code(error_code: str, message: str) -> DesktopAutomationError:
    """Map a `DesktopCommandResult.error_code` to its specific exception type.

    Falls back to the generic `DesktopCommandFailedError` for any code this
    client version does not (yet) recognize, rather than raising a `KeyError`
    — an older client must still fail closed and informatively against a
    newer agent's error vocabulary.
    """
    exception_type = _ERROR_CODE_MAP.get(error_code, DesktopCommandFailedError)
    return exception_type(message, error_code=error_code)


__all__ = [
    "DesktopApplicationNotAllowedError",
    "DesktopAutomationError",
    "DesktopCommandFailedError",
    "DesktopElementAmbiguousError",
    "DesktopElementNotFoundError",
    "DesktopInvalidSelectorError",
    "DesktopLaunchFailedError",
    "DesktopWindowNotFoundError",
    "error_for_code",
]
