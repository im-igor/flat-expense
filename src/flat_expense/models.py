from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
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
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"))
    # Empty string for roots allows a portable UNIQUE constraint (NULL != NULL).
    parent_key: Mapped[str] = mapped_column(String(36), default="")
    __table_args__ = (UniqueConstraint("owner_id", "parent_key", "name"),)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(500))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    expected_minor: Mapped[int | None] = mapped_column(BigInteger)
    agreed_minor: Mapped[int | None] = mapped_column(BigInteger)
    due_month: Mapped[date | None] = mapped_column(Date)
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
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    description: Mapped[str] = mapped_column(String(500))
    note: Mapped[str] = mapped_column(String(4000), default="")
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), index=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        Index("expense_owner_date", "owner_id", "paid_on"),
    )


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequestLog(Base):
    __tablename__ = "requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("owner_id", "key"),)


class Draft(Base):
    __tablename__ = "drafts"
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class BotCursor(Base):
    __tablename__ = "bot_cursor"
    id: Mapped[int] = mapped_column(primary_key=True)
    next_update_id: Mapped[int] = mapped_column(BigInteger, default=0)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    source_sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("owner_id", "source_sha256"),)
