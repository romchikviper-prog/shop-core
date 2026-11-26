"""Команди, події та чисте ядро Event Sourcing для замовлення.

`decide` не виконує зовнішніх викликів: він лише перевіряє інваріанти
й повертає нові події. `evolve` відновлює стан із події. Завдяки цьому
журнал можна переграти, а тести не потребують мережі.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, TypeAlias, assert_never
from uuid import UUID

from shop.domain.errors import (
    CurrencyMismatch,
    DomainError,
    EmptyOrder,
    InvalidState,
    InvalidValue,
    PaymentUnavailable,
    TotalMismatch,
)
from shop.domain.ids import CustomerId, OrderId, Sku
from shop.domain.money import Currency, Money
from shop.domain.order import OrderLine
from shop.domain.payment import PaymentReceipt
from shop.domain.result import Err, Ok, Result

from .jsonutil import (
    JsonObject,
    as_int,
    as_list,
    as_object,
    as_str,
    datetime_from_json,
    datetime_to_json,
    field,
    money_from_json,
    money_to_json,
)

OrderStatus: TypeAlias = Literal["new", "placed", "paid", "cancelled"]


def _require_aware(value: datetime, what: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{what} має містити часову зону")


def _require_text(value: str, what: str) -> None:
    if not value.strip():
        raise ValueError(f"{what} не може бути порожнім")


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    """Оформити замовлення: створити подію OrderPlaced."""

    command_id: str
    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    at: datetime

    def __post_init__(self) -> None:
        _require_text(self.command_id, "command_id")
        _require_aware(self.at, "at")


@dataclass(frozen=True, slots=True)
class PayOrder:
    """Оплатити замовлення.

    Підтвердження платежу з'являється лише після успішного виклику
    залежного сервісу. Тому `receipt` заповнює застосунковий шар, а
    `decide` перевіряє його узгодженість із сумою замовлення.
    """

    command_id: str
    order_id: OrderId
    at: datetime
    receipt: PaymentReceipt | None = None

    def __post_init__(self) -> None:
        _require_text(self.command_id, "command_id")
        _require_aware(self.at, "at")


@dataclass(frozen=True, slots=True)
class CancelOrder:
    """Скасувати неоплачене замовлення."""

    command_id: str
    order_id: OrderId
    reason: str
    at: datetime

    def __post_init__(self) -> None:
        _require_text(self.command_id, "command_id")
        _require_text(self.reason, "reason")
        _require_aware(self.at, "at")


OrderCommand: TypeAlias = PlaceOrder | PayOrder | CancelOrder


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    """Замовлення оформлено."""

    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    total: Money
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at, "at")


@dataclass(frozen=True, slots=True)
class PaymentCaptured:
    """Кошти за замовлення успішно списано."""

    order_id: OrderId
    amount: Money
    transaction_id: str
    at: datetime

    def __post_init__(self) -> None:
        _require_text(self.transaction_id, "transaction_id")
        _require_aware(self.at, "at")


@dataclass(frozen=True, slots=True)
class OrderCancelled:
    """Замовлення скасовано."""

    order_id: OrderId
    reason: str
    at: datetime

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason")
        _require_aware(self.at, "at")


OrderEvent: TypeAlias = OrderPlaced | PaymentCaptured | OrderCancelled


@dataclass(frozen=True, slots=True)
class OrderState:
    """Стан агрегату, відновлений із журналу."""

    order_id: OrderId
    status: OrderStatus
    customer_id: CustomerId | None
    lines: tuple[OrderLine, ...]
    total: Money | None
    version: int
    updated_at: datetime | None

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ValueError("version не може бути від'ємною")
        if self.updated_at is not None:
            _require_aware(self.updated_at, "updated_at")


def initial_state(order_id: OrderId) -> OrderState:
    """Порожній стан нового потоку."""
    return OrderState(
        order_id=order_id,
        status="new",
        customer_id=None,
        lines=(),
        total=None,
        version=0,
        updated_at=None,
    )


def decide(state: OrderState, command: OrderCommand) -> Result[tuple[OrderEvent, ...], DomainError]:
    """Перевіряє команду й повертає нові події або доменну помилку."""
    match command:
        case PlaceOrder(customer_id=customer_id, lines=lines, at=at):
            if state.status != "new":
                return Err(InvalidState("new", state.status))
            if not lines:
                return Err(EmptyOrder())
            currency = lines[0].unit_price.currency
            total = Money.zero(currency)
            for line in lines:
                if line.unit_price.currency != currency:
                    return Err(CurrencyMismatch(currency, line.unit_price.currency))
                total = total.plus(line.total)
            if total.is_zero():
                return Err(InvalidValue("total", "сума замовлення має бути додатною"))
            placed = OrderPlaced(
                order_id=state.order_id,
                customer_id=customer_id,
                lines=lines,
                total=total,
                at=at,
            )
            return Ok((placed,))

        case PayOrder(receipt=receipt, at=at):
            if state.status != "placed":
                return Err(InvalidState("placed", state.status))
            if state.total is None:
                return Err(InvalidState("placed із сумою", state.status))
            if receipt is None:
                return Err(PaymentUnavailable("немає підтвердження платежу"))
            if receipt.amount != state.total:
                return Err(TotalMismatch(state.total, receipt.amount))
            captured = PaymentCaptured(
                order_id=state.order_id,
                amount=state.total,
                transaction_id=receipt.transaction_id,
                at=at,
            )
            return Ok((captured,))

        case CancelOrder(reason=reason, at=at):
            if state.status != "placed":
                return Err(InvalidState("placed", state.status))
            cancelled = OrderCancelled(order_id=state.order_id, reason=reason, at=at)
            return Ok((cancelled,))

        case _:
            assert_never(command)


def evolve(state: OrderState, event: OrderEvent) -> OrderState:
    """Застосовує подію до стану; некоректний журнал падає явно."""
    if event.order_id != state.order_id:
        raise ValueError(
            f"подія для {event.order_id} не належить потоку {state.order_id}"
        )
    match event:
        case OrderPlaced(customer_id=customer_id, lines=lines, total=total, at=at):
            if state.status != "new":
                raise ValueError(f"OrderPlaced недопустима у стані {state.status}")
            return replace(
                state,
                status="placed",
                customer_id=customer_id,
                lines=lines,
                total=total,
                version=state.version + 1,
                updated_at=at,
            )
        case PaymentCaptured(amount=amount, at=at):
            if state.status != "placed":
                raise ValueError(f"PaymentCaptured недопустима у стані {state.status}")
            if state.total is None or amount != state.total:
                raise ValueError("сума платежу не збігається із сумою замовлення")
            return replace(
                state,
                status="paid",
                version=state.version + 1,
                updated_at=at,
            )
        case OrderCancelled(at=at):
            if state.status != "placed":
                raise ValueError(f"OrderCancelled недопустима у стані {state.status}")
            return replace(
                state,
                status="cancelled",
                version=state.version + 1,
                updated_at=at,
            )
        case _:
            assert_never(event)


def fold_events(order_id: OrderId, events: Sequence[OrderEvent]) -> OrderState:
    """Відновлює стан послідовним застосуванням подій."""
    state = initial_state(order_id)
    for event in events:
        state = evolve(state, event)
    return state


def event_type_name(event: OrderEvent) -> str:
    """Стабільна назва типу події для журналу."""
    match event:
        case OrderPlaced():
            return "OrderPlaced"
        case PaymentCaptured():
            return "PaymentCaptured"
        case OrderCancelled():
            return "OrderCancelled"
        case _:
            assert_never(event)


def event_to_json(event: OrderEvent) -> JsonObject:
    """Серіалізує подію для JSON Lines."""
    match event:
        case OrderPlaced(
            order_id=order_id,
            customer_id=customer_id,
            lines=lines,
            total=total,
            at=at,
        ):
            return {
                "orderId": str(order_id),
                "customerId": str(customer_id),
                "lines": [line_to_json(line) for line in lines],
                "total": money_to_json(total),
                "at": datetime_to_json(at),
            }
        case PaymentCaptured(
            order_id=order_id,
            amount=amount,
            transaction_id=transaction_id,
            at=at,
        ):
            return {
                "orderId": str(order_id),
                "amount": money_to_json(amount),
                "transactionId": transaction_id,
                "at": datetime_to_json(at),
            }
        case OrderCancelled(order_id=order_id, reason=reason, at=at):
            return {
                "orderId": str(order_id),
                "reason": reason,
                "at": datetime_to_json(at),
            }
        case _:
            assert_never(event)


def event_from_json(event_type: str, value: object) -> OrderEvent:
    """Відновлює подію з журналу."""
    payload = as_object(value, "event")
    match event_type:
        case "OrderPlaced":
            return OrderPlaced(
                order_id=order_id_from_text(as_str(field(payload, "orderId"), "orderId")),
                customer_id=customer_id_from_text(
                    as_str(field(payload, "customerId"), "customerId")
                ),
                lines=tuple(
                    line_from_json(item)
                    for item in as_list(field(payload, "lines"), "lines")
                ),
                total=money_from_json(field(payload, "total"), "total"),
                at=datetime_from_json(field(payload, "at"), "at"),
            )
        case "PaymentCaptured":
            return PaymentCaptured(
                order_id=order_id_from_text(as_str(field(payload, "orderId"), "orderId")),
                amount=money_from_json(field(payload, "amount"), "amount"),
                transaction_id=as_str(
                    field(payload, "transactionId"), "transactionId"
                ),
                at=datetime_from_json(field(payload, "at"), "at"),
            )
        case "OrderCancelled":
            return OrderCancelled(
                order_id=order_id_from_text(as_str(field(payload, "orderId"), "orderId")),
                reason=as_str(field(payload, "reason"), "reason"),
                at=datetime_from_json(field(payload, "at"), "at"),
            )
        case _:
            raise ValueError(f"невідомий тип події: {event_type!r}")


def line_to_json(line: OrderLine) -> JsonObject:
    """Серіалізує позицію замовлення."""
    return {
        "sku": line.sku.value,
        "name": line.name,
        "unitPrice": money_to_json(line.unit_price),
        "quantity": line.quantity,
    }


def line_from_json(value: object) -> OrderLine:
    """Розбирає позицію замовлення."""
    payload = as_object(value, "line")
    sku_text = as_str(field(payload, "sku"), "sku")
    try:
        sku = Sku(sku_text)
    except ValueError as exc:
        raise ValueError(f"sku: {exc}") from exc
    return OrderLine(
        sku=sku,
        name=as_str(field(payload, "name"), "name"),
        unit_price=money_from_json(field(payload, "unitPrice"), "unitPrice"),
        quantity=as_int(field(payload, "quantity"), "quantity"),
    )


def order_id_from_text(text: str) -> OrderId:
    """Розбирає UUID замовлення для внутрішніх даних."""
    try:
        return OrderId(UUID(text))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"некоректний orderId: {text!r}") from exc


def customer_id_from_text(text: str) -> CustomerId:
    """Розбирає UUID покупця для внутрішніх даних."""
    try:
        return CustomerId(UUID(text))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"некоректний customerId: {text!r}") from exc


def state_to_json(state: OrderState) -> JsonObject:
    """Подання стану для запиту читання ядра."""
    total: str | None = None
    currency: str | None = None
    if state.total is not None:
        total = f"{state.total.amount:.2f}"
        currency = state.total.currency.value
    return {
        "orderId": str(state.order_id),
        "status": state.status,
        "version": state.version,
        "customerId": None if state.customer_id is None else str(state.customer_id),
        "total": total,
        "currency": currency,
        "lineCount": len(state.lines),
        "updatedAt": None if state.updated_at is None else datetime_to_json(state.updated_at),
    }



