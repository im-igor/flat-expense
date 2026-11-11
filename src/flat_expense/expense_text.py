"""Conservative extraction of explicit ruble amounts and dates; never invent money."""

import re
from datetime import date, timedelta
from decimal import Decimal

from flat_expense import service as s

DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b")
NUMBER = re.compile(r"(?<![\w.,+-])(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?(?![\d.,])")
UNIT = re.compile(
    r"^\s*(тыс(?:яч[а-я]*)?\.?|[кk](?![а-яa-z])|млн\.?)?\s*(₽|руб(?:[а-я]*|\.)|р\.(?!\w))?", re.I
)
QUANTITY = re.compile(r"^\s*(?:шт\b|штук|бан(?:ок|к[аиу])|мешк|кг\b|м[²2]\b|литр|метр)", re.I)
FUTURE = re.compile(
    r"\b(?:завтра|планирую|куплю|купить|заплачу|оплачу|буду|нужно|надо|возврат|вернули|долг)\b",
    re.I,
)
PAYMENT = re.compile(
    r"\b(?:оплат\w*|заплат\w*|потрат\w*|купил[аи]?|аванс|расход|перев[её]л\w*)\b", re.I
)


def extract_leading_payment(text: str, today: date) -> dict:
    """Parse the fast ``amount + description`` format without inference.

    Only the first number is money by definition. Later numbers stay in the
    description, so a quantity such as ``3 алмазные чашки`` is not a payment.
    """
    original = text.strip()
    values = {"description": original[:500], "note": original if len(original) > 500 else ""}
    match = NUMBER.match(original)
    if match is None:
        return {"is_expense": False, "values": values}

    suffix = original[match.end() :]
    unit = UNIT.match(suffix)
    scale, _currency = unit.groups()
    try:
        number = Decimal(re.sub(r"[ \u00a0\u202f]", "", match.group()).replace(",", "."))
        if scale:
            number *= 1_000_000 if scale.lower().startswith("млн") else 1000
        amount = s.minor(number)
    except (s.DomainError, ArithmeticError):
        return {"is_expense": False, "values": values}
    if not amount:
        return {"is_expense": False, "values": values}

    description = suffix[unit.end() :].strip(" \t—–:-")
    if not description:
        return {"is_expense": False, "values": values}
    values.update(
        {
            "amount": s.money(amount),
            "paid_on": today.isoformat(),
            "description": description[:500],
            "note": description if len(description) > 500 else "",
        }
    )
    return {"is_expense": True, "values": values}


def extract(text: str, today: date) -> dict:
    text = text.strip()
    values = {"description": text[:500], "note": text if len(text) > 500 else ""}
    # Foreign currency, refunds and future intentions require the manual flow.
    if FUTURE.search(text) or re.search(r"[$€]|\b(?:usd|eur|доллар\w*|евро)\b", text, re.I):
        return {"is_expense": False, "values": values}

    dates = DATE.findall(text)
    if len(set(dates)) == 1:
        try:
            values["paid_on"] = s.parse_date(dates[0]).isoformat()
        except s.DomainError:
            pass
    elif not dates:
        lowered = text.lower()
        if "позавчера" in lowered:
            values["paid_on"] = (today - timedelta(days=2)).isoformat()
        elif "вчера" in lowered:
            values["paid_on"] = (today - timedelta(days=1)).isoformat()
        elif not re.search(
            r"\b(?:завтра|недел\w*|в\s+(?:январ\w*|феврал\w*|март\w*|апрел\w*|мае|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*))\b",
            lowered,
        ):
            values["paid_on"] = today.isoformat()

    without_dates = DATE.sub(lambda m: " " * len(m.group()), text)
    candidates = []
    for match in NUMBER.finditer(without_dates):
        suffix = without_dates[match.end() :]
        unit = UNIT.match(suffix)
        scale, currency = unit.groups()
        if not scale and not currency and QUANTITY.match(suffix):
            continue
        try:
            number = Decimal(re.sub(r"[ \u00a0\u202f]", "", match.group()).replace(",", "."))
            if scale:
                number *= 1_000_000 if scale.lower().startswith("млн") else 1000
            amount = s.minor(number)
            if not amount:
                continue
        except (s.DomainError, ArithmeticError):
            # A malformed amount must be corrected by the user, never rounded.
            return {"is_expense": True, "values": values}
        # Exclude a quoted full price from an explicitly marked advance/payment.
        prefix = without_dates[: match.start()]
        is_total = bool(
            re.search(
                r"(?:полная\s+(?:стоимость|цена)|всего|план(?:овая)?\s+(?:стоимость|цена))[^\d]{0,30}$",
                prefix,
                re.I,
            )
        )
        candidates.append((amount, bool(scale or currency), is_total))

    payments = [c for c in candidates if not c[2]]
    explicit = [c for c in payments if c[1]]
    # Do not silently drop an unmarked second payment if another has a currency suffix.
    if len(payments) > 1:
        return {"is_expense": False, "values": values}
    selected = explicit or payments
    if len(selected) == 1:
        values["amount"] = s.money(selected[0][0])
    return {"is_expense": bool(selected or PAYMENT.search(text)), "values": values}
