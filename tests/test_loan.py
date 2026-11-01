from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook

from flat_expense import service as s
from flat_expense.cbr import parse_key_rates
from flat_expense.importer import LOAN_HEADERS, import_loan_xlsx
from flat_expense.models import LoanPeriod


def loan_book(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Финплан по потокам"
    sheet.append([])
    sheet.append([])
    sheet.append(LOAN_HEADERS)
    sheet.append(
        [
            datetime(2026, 4, 1),
            365,
            30,
            0,
            datetime(2026, 5, 10),
            0.145,
            29_170_000,
            69_528.49,
            0,
            69_528.49,
            29_170_000,
        ]
    )
    sheet.append(
        [
            datetime(2026, 5, 1),
            365,
            31,
            1,
            datetime(2026, 6, 10),
            "#N/A",
            29_170_000,
            72_925,
            208_742.69,
            281_667.69,
            28_961_257.31,
        ]
    )
    book.save(path)


def test_loan_import_and_excel_formula(sessions, tmp_path):
    path = tmp_path / "loan.xlsx"
    loan_book(path)
    with sessions() as db, db.begin():
        result = import_loan_xlsx(db, 111, path)
        assert result["periods"] == 2
        rows = s.list_loan_periods(db, 111)
        assert rows[0]["key_rate"] == "14.5000"
        assert rows[0]["material_benefit"] == "159835.62"
        assert rows[0]["material_tax"] == "55942.47"
        assert rows[0]["total"] == "125470.96"
    with sessions() as db, db.begin():
        assert import_loan_xlsx(db, 111, path)["already_imported"]


def test_refresh_uses_historical_and_forecast_rates(sessions, monkeypatch):
    with sessions() as db, db.begin():
        db.add_all(
            [
                LoanPeriod(
                    owner_id=111,
                    usage_month=date(2026, 4, 1),
                    period_index=0,
                    payment_date=date(2026, 5, 10),
                    start_balance_minor=100_000,
                    interest_minor=1_000,
                    principal_minor=0,
                    payment_minor=1_000,
                    end_balance_minor=100_000,
                ),
                LoanPeriod(
                    owner_id=111,
                    usage_month=date(2026, 6, 1),
                    period_index=1,
                    payment_date=date(2026, 7, 10),
                    start_balance_minor=100_000,
                    interest_minor=1_000,
                    principal_minor=1_000,
                    payment_minor=2_000,
                    end_balance_minor=99_000,
                ),
            ]
        )
    monkeypatch.setattr(s, "today", lambda: date(2026, 6, 1))

    def fake_rates(start, end):
        return {date(2026, 5, 8): Decimal("14.50"), date(2026, 6, 1): Decimal("14.25")}

    with sessions() as db, db.begin():
        result = s.refresh_loan_rates(db, 111, fake_rates)
        rows = s.list_loan_periods(db, 111)
        assert result == {
            "updated": 2,
            "actual": 1,
            "forecast": 1,
            "latest_rate": "14.2500",
            "latest_rate_date": "2026-06-01",
            "source": "https://www.cbr.ru/hd_base/KeyRate/",
        }
        assert (rows[0]["key_rate"], rows[0]["rate_kind"]) == ("14.5000", "actual")
        assert (rows[1]["key_rate"], rows[1]["rate_kind"]) == ("14.2500", "forecast")


def test_loan_payments_are_in_monthly_expense_analysis(sessions, monkeypatch):
    monkeypatch.setattr(s, "today", lambda: date(2026, 6, 1))
    with sessions() as db, db.begin():
        for index, payment_date in enumerate((date(2026, 5, 10), date(2026, 7, 10))):
            db.add(
                LoanPeriod(
                    owner_id=111,
                    usage_month=payment_date.replace(day=1),
                    period_index=index,
                    payment_date=payment_date,
                    key_rate=Decimal("15"),
                    start_balance_minor=1_000,
                    interest_minor=1_000,
                    principal_minor=0,
                    payment_minor=1_000,
                    end_balance_minor=0,
                )
            )
        result = s.summary(db, 111, date(2026, 5, 1), date(2026, 7, 1))
        assert result["months"][0]["actual"] == "10.00"
        assert result["months"][2]["planned_remaining"] == "10.00"
        assert result["loan_actual"] == result["loan_planned"] == "10.00"


def test_parse_official_key_rate_response():
    payload = b"""<?xml version="1.0"?><soap:Envelope xmlns:soap="urn:soap">
      <soap:Body><KeyRateXMLResponse><KeyRateXMLResult><KeyRate>
        <KR><DT>2026-10-30T00:00:00+03:00</DT><Rate>14.00</Rate></KR>
      </KeyRate></KeyRateXMLResult></KeyRateXMLResponse></soap:Body></soap:Envelope>"""
    assert parse_key_rates(payload) == {date(2026, 10, 30): Decimal("14.00")}
