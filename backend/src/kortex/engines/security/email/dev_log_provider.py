"""`DevLogEmailProvider` — the only `IEmailProvider` implementation that
exists today (Phase A). Not real delivery: logs the full rendered email at
INFO (including any reset link — this is local-only developer tooling) and
appends it as one JSON line to `dev_outbox.jsonl` in sandboxed file storage
(`IFileStore`, never raw filesystem I/O — the same boundary every other
Security Engine component keeps), so a human or a test can retrieve the
last "sent" email deterministically. Selected whenever `KORTEX_EMAIL_PROVIDER`
is unset or `"dev_log"` — the only value that currently resolves to anything.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.security.email.base import IEmailProvider
from kortex.engines.storage.interfaces import IFileStore

logger = logging.getLogger("kortex.engines.security.email.dev_log")

_OUTBOX_PATH = "dev_outbox.jsonl"


class DevLogEmailProvider(IEmailProvider):
    """Development-only email provider: logs + appends to a local outbox file.

    Never used silently without disclosure — logs a one-time WARNING per
    process that this is not real delivery.
    """

    def __init__(self, file_store: IFileStore) -> None:
        self._file_store = file_store
        self._warned = False

    async def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        if not self._warned:
            logger.warning(
                "DevLogEmailProvider is active: emails are NOT being delivered. "
                "Configure KORTEX_EMAIL_PROVIDER with a real provider before relying on email delivery."
            )
            self._warned = True

        entry = {
            "to": to,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "sent_at_utc": datetime.now(UTC).isoformat(),
        }
        logger.info("DevLogEmailProvider: email to=%s subject=%r body=%r", to, subject, body_text)

        try:
            existing = await self._file_store.read_file(_OUTBOX_PATH)
        except ResourceNotFoundError:
            existing = b""

        line = (json.dumps(entry) + "\n").encode("utf-8")
        await self._file_store.write_file(_OUTBOX_PATH, existing + line)
