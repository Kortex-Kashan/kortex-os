"""Invoice persistence manager for the KORTEX Finance business module.

Mirrors `kortex.engines.knowledge.lineage.KnowledgeLineageManager`'s own
persistence discipline: writes go directly through `IDataStore.
execute_in_transaction`, raise on failure rather than silently continuing
with an in-memory-only fallback ("a persistence failure must never be
silently swallowed"), and no `ICacheStore` tier is used for this minimal
slice -- no evidence a first pilot capability needs one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.finance.models import CreateInvoiceRequest, FinanceInvoice, InvoiceStatus
from kortex.modules.finance.persistence import FinanceInvoiceRow


class FinanceInvoiceManager:
    """Creates and durably persists Finance Invoices via `IDataStore`."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    async def create_invoice(self, request: CreateInvoiceRequest, tenant_id: str) -> FinanceInvoice:
        """Persist a new DRAFT invoice for `tenant_id`.

        `tenant_id` is a required positional argument the caller (the
        capability handler) must derive from `principal.tenant_id` --
        this method has no `tenant_id` field to trust from `request`
        because `CreateInvoiceRequest` never carries one (see models.py).
        """
        invoice_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)

        async def _action(session: AsyncSession) -> None:
            session.add(
                FinanceInvoiceRow(
                    id=invoice_id,
                    tenant_id=tenant_id,
                    customer_name=request.customer_name,
                    amount=request.amount,
                    currency=request.currency,
                    due_date=request.due_date,
                    status=InvoiceStatus.DRAFT.value,
                    invoice_created_at=created_at,
                )
            )

        await self._data_store.execute_in_transaction(_action)

        return FinanceInvoice(
            invoice_id=invoice_id,
            tenant_id=tenant_id,
            customer_name=request.customer_name,
            amount=request.amount,
            currency=request.currency,
            due_date=request.due_date,
            status=InvoiceStatus.DRAFT,
            created_at=created_at,
        )


__all__ = ["FinanceInvoiceManager"]
