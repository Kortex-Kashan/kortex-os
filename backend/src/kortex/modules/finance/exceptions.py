"""Exception hierarchy for the KORTEX Finance business module.

Inherits from `kortex.core.exceptions.KortexError`, per that module's own
docstring: "All exception types raised by KORTEX Core, System Engines, and
Modules inherit from the base `KortexError` exception class" -- Modules are
explicitly named as an intended consumer of this existing hierarchy, so
this is participation in an already-established convention (mirroring
`KnowledgeEngineError(KortexError)`'s pattern exactly), not a new exception
framework.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class FinanceModuleError(KortexError):
    """Base exception for all Finance module errors."""


class FinanceInvoiceValidationError(FinanceModuleError):
    """Raised when invoice creation input fails validation."""


class FinanceInvoiceNotFoundError(FinanceModuleError):
    """Raised when a requested invoice is not found.

    Raised identically whether the `invoice_id` genuinely does not exist or
    exists under a different tenant -- mirrors `ConnectorProfileManager.
    get_profile`'s established enumeration-resistance convention
    (`kortex.engines.connector.profiles`, M6.3-1): a caller can never
    distinguish "wrong tenant" from "no such invoice" from this error alone.
    """


__all__ = [
    "FinanceInvoiceNotFoundError",
    "FinanceInvoiceValidationError",
    "FinanceModuleError",
]
