"""Pydantic v2 domain models for the KORTEX Finance business module.

Scope: exactly the fields needed for `kortex.finance.invoice.create` (see
the Finance-pilot planning pass preceding this commit). `Invoice` is
catalogued in `docs/architecture/business_entity_model.md` (Approved
Architecture) as `kortex.finance.invoice`, "Commercial billing invoice
entity (Immutable when Published)". This implementation only ever creates
`DRAFT` invoices -- Published-state immutability/publication workflow is
explicitly out of scope for this slice (see module docstring in
`module.py`).

Deliberately excludes the full 10-facet `UniversalEntity` framework
(`business_entity_model.md` §3: Relationships, Versioning, Classification,
Search, a formal `UniversalValidationReport` object) -- none of those 10
facets have any Python implementation anywhere in this repository yet
(confirmed by repository-wide search during planning), so requiring all of
them for one pilot capability would itself be a platform-scale
undertaking. Identity, Metadata, Ownership (`tenant_id`), and Lifecycle
(a single `DRAFT` status) are the facets this minimal slice actually
needs and implements; Audit is already satisfied for free by the existing,
generic `CapabilityDispatcher._audit_execution` mechanism (confirmed real
in `security/models.py::UniversalAuditEntry`), so no per-entity audit
trail is built here either.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ISO_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class InvoiceStatus(str, Enum):
    """Lifecycle status for a Finance Invoice.

    Only `DRAFT` is ever produced by this slice. `PUBLISHED` is catalogued
    by `business_entity_model.md` ("Immutable when Published") but no
    publication capability exists yet -- listed here only so the field's
    type is honest about the entity's eventual full lifecycle, not as an
    implemented transition.
    """

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class CreateInvoiceRequest(BaseModel):
    """Request payload for `kortex.finance.invoice.create`.

    Deliberately carries no `tenant_id` field -- tenant ownership is never
    caller-supplied for this capability; it is always
    `principal.tenant_id`-authoritative (see `module.py`). Any `tenant_id`
    a caller attempts to smuggle into `FinanceModule.create_invoice`'s
    keyword arguments is rejected by that method's own signature (no such
    parameter exists to accept it), not merely overridden after the fact.
    """

    customer_name: str = Field(min_length=1, description="Name of the customer being billed.")
    amount: Decimal = Field(gt=0, description="Invoice total, strictly positive.")
    currency: str = Field(description="3-letter ISO 4217 currency code, e.g. 'USD'.")
    due_date: date | None = Field(default=None, description="Optional payment due date.")

    @field_validator("customer_name")
    @classmethod
    def _customer_name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("customer_name cannot be blank or whitespace-only.")
        return stripped

    @field_validator("currency")
    @classmethod
    def _currency_is_iso_shaped(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _ISO_CURRENCY_PATTERN.match(normalized):
            raise ValueError(f"currency must be a 3-letter ISO 4217 code, got {value!r}.")
        return normalized


class FinanceInvoice(BaseModel):
    """A persisted Finance Invoice (`kortex.finance.invoice`), always DRAFT
    for this slice. Immutable snapshot returned to the caller -- mutation
    happens only via a new persistence write, never in place."""

    model_config = ConfigDict(frozen=True)

    invoice_id: str
    tenant_id: str
    customer_name: str
    amount: Decimal
    currency: str
    due_date: date | None
    status: InvoiceStatus
    created_at: datetime


__all__ = ["CreateInvoiceRequest", "FinanceInvoice", "InvoiceStatus"]
