"""Unit tests for `kortex.modules.finance.models.CreateInvoiceRequest`
validation (Finance-pilot planning pass).

Pure Pydantic-level tests -- no Kernel, no Storage, no dispatch involved.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from kortex.modules.finance.models import CreateInvoiceRequest


def test_valid_invoice_request_constructs_successfully() -> None:
    request = CreateInvoiceRequest(
        customer_name="Acme Corp",
        amount=Decimal("1500.00"),
        currency="usd",
        due_date=date(2026, 12, 31),
    )
    assert request.customer_name == "Acme Corp"
    assert request.amount == Decimal("1500.00")
    assert request.currency == "USD"  # normalized to uppercase
    assert request.due_date == date(2026, 12, 31)


def test_valid_invoice_request_allows_omitted_due_date() -> None:
    request = CreateInvoiceRequest(customer_name="Acme Corp", amount=Decimal("10"), currency="EUR")
    assert request.due_date is None


def test_empty_customer_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInvoiceRequest(customer_name="", amount=Decimal("10"), currency="USD")


def test_whitespace_only_customer_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInvoiceRequest(customer_name="   ", amount=Decimal("10"), currency="USD")


def test_zero_amount_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInvoiceRequest(customer_name="Acme Corp", amount=Decimal("0"), currency="USD")


def test_negative_amount_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInvoiceRequest(customer_name="Acme Corp", amount=Decimal("-5"), currency="USD")


def test_invalid_currency_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInvoiceRequest(customer_name="Acme Corp", amount=Decimal("10"), currency="US")


def test_non_alphabetic_currency_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInvoiceRequest(customer_name="Acme Corp", amount=Decimal("10"), currency="12A")
