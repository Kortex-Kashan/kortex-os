"""`IEmailProvider` — the provider-agnostic email-delivery seam (Phase A).

Deliberately minimal: `send_email` is the only operation any caller in this
codebase needs (password-reset links today). A real SMTP/Resend/SendGrid
provider implements this exact protocol with no changes required anywhere
that calls it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IEmailProvider(Protocol):
    """Provider-agnostic outbound email abstraction."""

    async def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        """Send one email. Must raise on genuine delivery failure — never
        silently swallow one, since a caller (e.g. password reset) may rely
        on the send actually having been attempted."""
        ...
