from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"))
    # Empty string for roots allows a portable UNIQUE constraint (NULL != NULL).
    parent_key: Mapped[str] = mapped_column(String(36), default="")
    __table_args__ = (UniqueConstraint("parent_key", "name"),)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(500))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    expected_minor: Mapped[int | None] = mapped_column(BigInteger)
    agreed_minor: Mapped[int | None] = mapped_column(BigInteger)
    due_month: Mapped[date | None] = mapped_column(Date)
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    paid_on: Mapped[date | None] = mapped_column(Date)
    payment_status: Mapped[str] = mapped_column(String(20), default="planned")
    note: Mapped[str] = mapped_column(String(4000), default="")
    archived: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("expected_minor IS NULL OR expected_minor >= 0"),
        CheckConstraint("agreed_minor IS NULL OR agreed_minor >= 0"),
    )


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    paid_on: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    planned_amount_minor: Mapped[int] = mapped_column(BigInteger)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    description: Mapped[str] = mapped_column(String(500))
    note: Mapped[str] = mapped_column(String(4000), default="")
    needs_review: Mapped[bool] = mapped_column(default=False)
    deleted: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("planned_amount_minor > 0", name="ck_expenses_planned_amount_positive"),
        Index("expense_paid_date", "paid_on"),
    )


class Document(Base):
    """A private supporting file, stored with the accounting database.

    A document has at most one direct target.  This deliberately avoids a
    generic links table and preserves the application's simple plan model.
    """

    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    expense_id: Mapped[str | None] = mapped_column(ForeignKey("expenses.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default="web")
    note: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520"),
        CheckConstraint("(expense_id IS NULL) OR (plan_id IS NULL)"),
        CheckConstraint("source IN ('web', 'telegram', 'forwarded')"),
    )


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequestLog(Base):
    __tablename__ = "requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("key"),)


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class BotCursor(Base):
    __tablename__ = "bot_cursor"
    id: Mapped[int] = mapped_column(primary_key=True)
    next_update_id: Mapped[int] = mapped_column(BigInteger, default=0)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("source_sha256"),)


class LoanPeriod(Base):
    """One row of the loan schedule (columns A–K from the source workbook)."""

    __tablename__ = "loan_periods"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    usage_month: Mapped[date] = mapped_column(Date)
    period_index: Mapped[int] = mapped_column(Integer)
    payment_date: Mapped[date] = mapped_column(Date)
    key_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    rate_kind: Mapped[str] = mapped_column(String(20), default="imported")
    rate_effective_on: Mapped[date | None] = mapped_column(Date)
    start_balance_minor: Mapped[int] = mapped_column(BigInteger)
    interest_minor: Mapped[int] = mapped_column(BigInteger)
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    payment_minor: Mapped[int] = mapped_column(BigInteger)
    end_balance_minor: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("period_index"),
        CheckConstraint("period_index >= 0"),
        CheckConstraint("start_balance_minor >= 0"),
        CheckConstraint("interest_minor >= 0"),
        CheckConstraint("principal_minor >= 0"),
        CheckConstraint("payment_minor >= 0"),
        CheckConstraint("end_balance_minor >= 0"),
        CheckConstraint("key_rate IS NULL OR (key_rate >= 0 AND key_rate <= 100)"),
        Index("loan_payment_date", "payment_date"),
    )


class LoanImportBatch(Base):
    __tablename__ = "loan_import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("source_sha256"),)
