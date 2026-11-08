from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Annotated[
    Decimal, Field(ge=0, le=Decimal("9999999999.99"), max_digits=12, decimal_places=2)
]
Payment = Annotated[
    Decimal, Field(gt=0, le=Decimal("9999999999.99"), max_digits=12, decimal_places=2)
]
Text = Annotated[str, Field(min_length=1, max_length=500)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class CategoryIn(Input):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class CategoryRenameIn(Input):
    name: str = Field(min_length=1, max_length=120)


class CategoryReplaceIn(Input):
    target_id: str


class ExpenseIn(Input):
    paid_on: date
    amount: Payment
    planned_amount: Payment | None = None
    category_id: str
    description: Text
    note: str = Field(default="", max_length=4000)


class ExpenseEdit(ExpenseIn):
    version: int = Field(ge=1)


class VersionIn(Input):
    version: int = Field(ge=1)


class PlanIn(Input):
    title: Text
    category_id: str
    expected: Money | None = None
    agreed: Money | None = None
    due_month: date | None = None
    note: str = Field(default="", max_length=4000)

    @field_validator("due_month")
    @classmethod
    def first_day(cls, value):
        return value.replace(day=1) if value else None


class PlanEdit(PlanIn):
    version: int = Field(ge=1)


class PlanDueMonthBulkEdit(Input):
    ids: list[str] = Field(min_length=1, max_length=500)
    due_month: date

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Каждый план можно выбрать только один раз.")
        return value

    @field_validator("due_month")
    @classmethod
    def first_day(cls, value):
        return value.replace(day=1)


class LoanRateIn(Input):
    key_rate: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    effective_on: date | None = None
