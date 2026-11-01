"""Official Bank of Russia key-rate client."""

from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

import httpx

ENDPOINT = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
SOURCE_PAGE = "https://www.cbr.ru/hd_base/KeyRate/"


class CbrError(RuntimeError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_key_rates(payload: bytes) -> dict[date, Decimal]:
    if len(payload) > 2_000_000:
        raise CbrError("Ответ Банка России слишком большой.")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise CbrError("Банк России вернул некорректный XML.") from exc
    result: dict[date, Decimal] = {}
    for row in (node for node in root.iter() if _local_name(node.tag) == "KR"):
        values = {_local_name(child.tag): child.text for child in row}
        try:
            day = date.fromisoformat((values["DT"] or "")[:10])
            rate = Decimal((values["Rate"] or "").replace(",", "."))
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise CbrError("В ответе Банка России неожиданный формат ставки.") from exc
        if not Decimal("0") <= rate <= Decimal("100"):
            raise CbrError("Банк России вернул недопустимое значение ставки.")
        result[day] = rate
    if not result:
        raise CbrError("Банк России не вернул данные о ключевой ставке.")
    return result


def fetch_key_rates(start: date, end: date) -> dict[date, Decimal]:
    if end < start:
        raise CbrError("Неверный период запроса ставки.")
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body><KeyRateXML xmlns="http://web.cbr.ru/">
    <fromDate>{start.isoformat()}T00:00:00</fromDate>
    <ToDate>{end.isoformat()}T23:59:59</ToDate>
  </KeyRateXML></soap:Body>
</soap:Envelope>"""
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "http://web.cbr.ru/KeyRateXML",
        "User-Agent": "flat-expense/0.1 key-rate updater",
    }
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.post(ENDPOINT, content=body.encode(), headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CbrError("Не удалось получить ставку с сайта Банка России.") from exc
    return parse_key_rates(response.content)
