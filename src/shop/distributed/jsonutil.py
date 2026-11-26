"""Робота з JSON і HTTP без зовнішніх залежностей.

Усі зовнішні дані перевіряються явно: замість довіри до `Any` після
`json.loads` функції повертають `object` і перетворюють його з
зрозумілою помилкою. Це тримає `mypy --strict` і робить межі сервісу
явними.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler
from typing import TypeAlias
from urllib.parse import parse_qs, urlsplit

from shop.domain.money import Currency, Money

JsonObject: TypeAlias = dict[str, object]


def as_object(value: object, what: str) -> JsonObject:
    """Перевіряє, що значення — JSON-об'єкт із рядковими ключами."""
    if not isinstance(value, dict):
        raise ValueError(f"{what}: очікується JSON-об'єкт")
    result: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{what}: ключі мають бути рядками")
        result[key] = item
    return result


def as_list(value: object, what: str) -> list[object]:
    """Перевіряє, що значення — JSON-масив."""
    if not isinstance(value, list):
        raise ValueError(f"{what}: очікується JSON-масив")
    return list(value)


def as_str(value: object, what: str) -> str:
    """Перевіряє рядок."""
    if not isinstance(value, str):
        raise ValueError(f"{what}: очікується рядок")
    return value


def as_int(value: object, what: str) -> int:
    """Перевіряє ціле число (bool не вважається числом)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what}: очікується ціле число")
    return value


def as_float(value: object, what: str) -> float:
    """Перевіряє число з рухомою комою."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what}: очікується число")
    return float(value)


def as_bool(value: object, what: str) -> bool:
    """Перевіряє логічне значення."""
    if not isinstance(value, bool):
        raise ValueError(f"{what}: очікується true або false")
    return value


def field(obj: JsonObject, key: str) -> object:
    """Повертає обов'язкове поле або кидає зрозумілу помилку."""
    if key not in obj:
        raise ValueError(f"відсутнє обов'язкове поле «{key}»")
    return obj[key]


def optional_field(obj: JsonObject, key: str) -> object | None:
    """Повертає необов'язкове поле; відсутнє та null — це None."""
    return obj.get(key)


def money_to_json(money: Money) -> JsonObject:
    """Серіалізує Money у стабільний JSON-об'єкт."""
    return {"amount": f"{money.amount:.2f}", "currency": money.currency.value}


def money_from_json(value: object, what: str = "money") -> Money:
    """Розбирає Money із JSON-об'єкта."""
    obj = as_object(value, what)
    amount_text = as_str(field(obj, "amount"), f"{what}.amount")
    currency_text = as_str(field(obj, "currency"), f"{what}.currency")
    try:
        amount = Decimal(amount_text)
    except InvalidOperation as exc:
        raise ValueError(f"{what}.amount: некоректне число {amount_text!r}") from exc
    try:
        currency = Currency(currency_text)
    except ValueError as exc:
        raise ValueError(f"{what}.currency: невідома валюта {currency_text!r}") from exc
    try:
        return Money(amount, currency)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what}: {exc}") from exc


def datetime_to_json(value: datetime) -> str:
    """Серіалізує час у ISO 8601."""
    return value.isoformat()


def datetime_from_json(value: object, what: str = "datetime") -> datetime:
    """Розбирає ISO 8601 і вимагає часову зону."""
    text = as_str(value, what)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{what}: некоректний час {text!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{what}: час має містити часову зону")
    return parsed


def read_json_body(handler: BaseHTTPRequestHandler) -> JsonObject:
    """Читає тіло HTTP-запиту як JSON-об'єкт."""
    length_text = handler.headers.get("Content-Length")
    if length_text is None:
        raise ValueError("відсутній заголовок Content-Length")
    try:
        length = int(length_text)
    except ValueError as exc:
        raise ValueError("некоректний Content-Length") from exc
    if length <= 0:
        raise ValueError("порожнє тіло запиту")
    if length > 1_000_000:
        raise ValueError("тіло запиту завелике")
    raw = handler.rfile.read(length)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("тіло запиту має бути UTF-8") from exc
    try:
        value = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"некоректний JSON: {exc.msg}") from exc
    return as_object(value, "тіло запиту")


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: JsonObject) -> None:
    """Надсилає JSON-відповідь; розірваний клієнт не валить сервер.

    Клієнт може закрити з'єднання через тайм-аут (особливо в режимі
    `slow`). Будь-яка помилка запису в сокет тут очікувана й не повинна
    перетворюватися на 500 або засмічувати лог трасуванням.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        handler.wfile.write(body)
    except OSError:
        pass


def send_error(
    handler: BaseHTTPRequestHandler,
    status: int,
    code: str,
    message: str,
) -> None:
    """Надсилає стандартну помилку API."""
    send_json(handler, status, {"error": {"code": code, "message": message}})


def split_path(path: str) -> tuple[str, dict[str, str]]:
    """Розділяє шлях і query string; останнє значення параметра перемагає."""
    parts = urlsplit(path)
    raw_query = parse_qs(parts.query)
    query = {key: values[-1] for key, values in raw_query.items() if values}
    return parts.path, query
