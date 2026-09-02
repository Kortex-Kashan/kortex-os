"""SQLAlchemy ORM persistence for the KORTEX Finance business module.

Follows the exact pattern established by Knowledge Engine's own persistence
module (`kortex.engines.knowledge.persistence`, M7): inherit
`core.db.BaseModel`, rely on the existing `Base.metadata.create_all()` boot
path (`DatabaseEngineManager.create_all_tables()`) for table creation via
the existing `StorageEngine`/`IDataStore` abstraction -- no new persistence
mechanism, no Alembic migration, no direct filesystem/database access
outside `IDataStore.execute_in_transaction`.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel


class FinanceInvoiceRow(BaseModel):
    """Durable row for one `FinanceInvoice` (`models.py`). Identity is
    `id` (the invoice_id); tenant ownership is enforced by scoping every
    query on `tenant_id`, never by trusting a caller-supplied value."""

    __tablename__ = "finance_invoices"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    due_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["FinanceInvoiceRow"]
