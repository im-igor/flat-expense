import secrets
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text

from flat_expense import service as s
from flat_expense.config import Settings, settings
from flat_expense.conversation import process_update
from flat_expense.db import make_sessions
from flat_expense.expense_ai import LocalExpenseAI
from flat_expense.models import BotCursor, Expense, Plan
from flat_expense.schemas import (
    CategoryIn,
    CategoryRenameIn,
    CategoryReplaceIn,
    ExpenseEdit,
    ExpenseIn,
    LoanRateIn,
    PlanDueMonthBulkEdit,
    PlanEdit,
    PlanIn,
    VersionIn,
)
from flat_expense.web import browser_actor, install_web


def create_app(config: Settings | None = None, sessions=None):
    config = config or settings()
    sessions = sessions or make_sessions(config.database_url)
    app = FastAPI(
        title="Flat Expense", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.sessions = sessions
    app.state.expense_ai = LocalExpenseAI(config) if config.expense_ai_enabled else None
    auth = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth)]):
        if credentials is None or not secrets.compare_digest(
            credentials.credentials, config.api_token.get_secret_value()
        ):
            raise HTTPException(401, "Требуется авторизация.")

    def actor(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth)],
        x_telegram_user_id: Annotated[int | None, Header()] = None,
    ):
        if credentials is None:
            return browser_actor(request, config)
        authorize(credentials)
        if x_telegram_user_id not in config.allowed_ids:
            raise HTTPException(403, "Доступ запрещён.")
        return x_telegram_user_id

    def session():
        with sessions() as db, db.begin():
            yield db

    @app.exception_handler(s.DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.get("/health")
    def health(db=Depends(session)):
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/openapi.json", dependencies=[Depends(authorize)])
    def openapi():
        return app.openapi()

    @app.get("/v1/categories")
    def categories(owner=Depends(actor), db=Depends(session)):
        return s.categories(db, owner)

    @app.post("/v1/categories/seed")
    def seed(owner=Depends(actor), db=Depends(session)):
        s.seed_categories(db, owner)
        return s.categories(db, owner)

    @app.post("/v1/categories")
    def create_category(data: CategoryIn, owner=Depends(actor), db=Depends(session)):
        return s.category_view(db, s.ensure_category(db, owner, data.name, data.parent_id))

    @app.put("/v1/categories/{identifier}")
    def rename_category(
        identifier: str,
        data: CategoryRenameIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "category.rename", "id": identifier, **data.model_dump()},
            lambda: s.rename_category(db, owner, identifier, data.name),
        )

    @app.delete("/v1/categories/{identifier}")
    def delete_category(
        identifier: str,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "category.delete", "id": identifier},
            lambda: s.delete_category(db, owner, identifier),
        )

    @app.post("/v1/categories/{identifier}/replace")
    def replace_category(
        identifier: str,
        data: CategoryReplaceIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "category.replace", "id": identifier, **data.model_dump()},
            lambda: s.replace_category(db, owner, identifier, data.target_id),
        )

    @app.get("/v1/expenses")
    def expenses(
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        deleted: bool = False,
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.list_expenses(db, owner, offset, limit, deleted)

    @app.get("/v1/expenses/{identifier}")
    def get_expense(identifier: str, owner=Depends(actor), db=Depends(session)):
        return s.expense_view(db, s.owned(db, Expense, owner, identifier))

    @app.post("/v1/expenses")
    def create_expense(
        data: ExpenseIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "expense.create", **data.model_dump(mode="json")},
            lambda: s.add_expense(db, owner, data),
        )

    @app.put("/v1/expenses/{identifier}")
    def edit_expense(
        identifier: str,
        data: ExpenseEdit,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "expense.update", "id": identifier, **data.model_dump(mode="json")},
            lambda: s.update_expense(db, owner, identifier, data, data.version),
        )

    @app.post("/v1/expenses/{identifier}/{action}")
    def toggle_expense(
        identifier: str,
        action: str,
        data: VersionIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        if action not in ("delete", "restore"):
            raise HTTPException(404)
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "expense." + action, "id": identifier, **data.model_dump()},
            lambda: s.toggle_expense(db, owner, identifier, data.version, action == "delete"),
        )

    @app.get("/v1/plans")
    def plans(
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        archived: bool = False,
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.list_plans(db, owner, offset, limit, archived)

    @app.get("/v1/plans/{identifier}")
    def get_plan(identifier: str, owner=Depends(actor), db=Depends(session)):
        return s.plan_view(db, s.owned(db, Plan, owner, identifier))

    @app.post("/v1/plans")
    def create_plan(
        data: PlanIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "plan.create", **data.model_dump(mode="json")},
            lambda: s.add_plan(db, owner, data),
        )

    @app.put("/v1/plans/{identifier}")
    def edit_plan(
        identifier: str,
        data: PlanEdit,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "plan.update", "id": identifier, **data.model_dump(mode="json")},
            lambda: s.update_plan(db, owner, identifier, data, data.version),
        )

    @app.post("/v1/plans/bulk-due-month")
    def bulk_move_plans(
        data: PlanDueMonthBulkEdit,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "plan.bulk_due_month", **data.model_dump(mode="json")},
            lambda: s.bulk_move_plan_due_month(db, owner, data.ids, data.due_month),
        )

    @app.delete("/v1/plans/{identifier}")
    def delete_plan(
        identifier: str,
        data: VersionIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "plan.delete", "id": identifier, **data.model_dump()},
            lambda: s.delete_plan(db, owner, identifier, data.version),
        )

    @app.post("/v1/plans/{identifier}/{action}")
    def toggle_plan(
        identifier: str,
        action: str,
        data: VersionIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        if action not in ("archive", "restore"):
            raise HTTPException(404)
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "plan." + action, "id": identifier, **data.model_dump()},
            lambda: s.toggle_plan(db, owner, identifier, data.version, action == "archive"),
        )

    @app.get("/v1/summary")
    def summary(start: date, end: date, owner=Depends(actor), db=Depends(session)):
        return s.summary(db, owner, start, end)

    @app.get("/v1/loan")
    def loan(owner=Depends(actor), db=Depends(session)):
        return s.list_loan_periods(db, owner)

    @app.post("/v1/loan/key-rates")
    def refresh_loan_rates(
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "loan.refresh_rates"},
            lambda: s.refresh_loan_rates(db, owner),
        )

    @app.put("/v1/loan/{identifier}/rate")
    def set_loan_rate(
        identifier: str,
        data: LoanRateIn,
        idempotency_key: Annotated[str, Header()],
        owner=Depends(actor),
        db=Depends(session),
    ):
        return s.once(
            db,
            owner,
            idempotency_key,
            {"action": "loan.set_rate", "id": identifier, **data.model_dump(mode="json")},
            lambda: s.set_loan_rate(db, owner, identifier, data.key_rate, data.effective_on),
        )

    @app.post("/internal/telegram/update", dependencies=[Depends(authorize)])
    def telegram(update: dict, db=Depends(session)):
        return process_update(db, config.allowed_ids, update, ai=app.state.expense_ai)

    @app.get("/internal/telegram/cursor", dependencies=[Depends(authorize)])
    def cursor(db=Depends(session)):
        record = db.get(BotCursor, 1)
        return {"offset": record.next_update_id if record else 0}

    @app.post("/internal/telegram/ack/{next_update_id}", dependencies=[Depends(authorize)])
    def acknowledge(next_update_id: int, db=Depends(session)):
        if next_update_id < 0:
            raise HTTPException(422)
        record = db.scalar(select(BotCursor).where(BotCursor.id == 1).with_for_update())
        if record is None:
            db.add(BotCursor(id=1, next_update_id=next_update_id))
        else:
            record.next_update_id = max(record.next_update_id, next_update_id)
        return {"ok": True}

    install_web(app, config)
    return app
