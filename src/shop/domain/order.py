"""Агрегат «Замовлення»: стани як окремі типи.

Кожен стан має лише ті переходи, які дозволені бізнес-правилами.
Наприклад, у Draft немає методу pay, а в Placed — методу ship, тому
недопустимий перехід не компілюється (його виявляє mypy), а не падає
в експлуатації.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Generic, TypeAlias, TypeVar, assert_never

from .delivery import DeliveryMethod, TrackingNumber
from .errors import CurrencyMismatch, DomainError, EmptyOrder, InvalidValue, TotalMismatch
from .events import OrderCancelled, OrderPaid, OrderPlaced, OrderShipped
from .ids import CustomerId, OrderId, Sku
from .money import Money
from .payment import PaymentMethod, PaymentReceipt
from .result import Err, Ok, Result

S = TypeVar("S")
E = TypeVar("E")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("час має бути з часовою зоною")


@dataclass(frozen=True, slots=True)
class Transition(Generic[S, E]):
    """Новий стан агрегату разом із подією, яка його спричинила."""

    state: S
    event: E


@dataclass(frozen=True, slots=True)
class OrderLine:
    """Позиція замовлення із зафіксованою ціною на момент додавання."""

    sku: Sku
    name: str
    unit_price: Money
    quantity: int

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("назва товару не може бути порожньою")
        if self.quantity < 1:
            raise ValueError("кількість має бути додатною")

    @property
    def total(self) -> Money:
        """Вартість позиції."""
        return self.unit_price.times(self.quantity)


@dataclass(frozen=True, slots=True)
class Draft:
    """Кошик: замовлення ще не оформлене."""

    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.created_at)

    def add_line(self, line: OrderLine) -> Result[Draft, DomainError]:
        """Додає товар; повторний артикул збільшує кількість."""
        if self.lines and line.unit_price.currency != self.lines[0].unit_price.currency:
            return Err(CurrencyMismatch(self.lines[0].unit_price.currency, line.unit_price.currency))
        merged: list[OrderLine] = []
        found = False
        for existing in self.lines:
            if existing.sku == line.sku:
                merged.append(
                    OrderLine(
                        sku=existing.sku,
                        name=line.name,
                        unit_price=line.unit_price,
                        quantity=existing.quantity + line.quantity,
                    )
                )
                found = True
            else:
                merged.append(existing)
        if not found:
            merged.append(line)
        return Ok(replace(self, lines=tuple(merged)))

    def remove_line(self, sku: Sku) -> Result[Draft, DomainError]:
        """Видаляє товар із кошика."""
        if not any(line.sku == sku for line in self.lines):
            return Err(InvalidValue("sku", f"товару {sku} немає в кошику"))
        return Ok(replace(self, lines=tuple(line for line in self.lines if line.sku != sku)))

    def place(
        self,
        total: Money,
        delivery: DeliveryMethod,
        payment: PaymentMethod,
        at: datetime,
    ) -> Result[Transition[Placed, OrderPlaced], DomainError]:
        """Оформлює замовлення: Draft -> Placed."""
        if not self.lines:
            return Err(EmptyOrder())
        currency = self.lines[0].unit_price.currency
        if total.currency != currency:
            return Err(CurrencyMismatch(currency, total.currency))
        if delivery.price.currency != currency:
            return Err(CurrencyMismatch(currency, delivery.price.currency))
        if total.amount <= 0:
            return Err(InvalidValue("total", "сума замовлення має бути додатною"))
        if at < self.created_at:
            return Err(InvalidValue("at", "час оформлення не може бути раніше створення"))
        placed = Placed(
            order_id=self.order_id,
            customer_id=self.customer_id,
            lines=self.lines,
            total=total,
            delivery=delivery,
            payment=payment,
            placed_at=at,
        )
        return Ok(Transition(placed, OrderPlaced(self.order_id, total, at)))

    def cancel(self, reason: str, at: datetime) -> Result[Transition[Cancelled, OrderCancelled], DomainError]:
        """Скасовує ще не оформлене замовлення."""
        if not reason.strip():
            return Err(InvalidValue("reason", "причина скасування обов'язкова"))
        if at < self.created_at:
            return Err(InvalidValue("at", "час скасування не може бути раніше створення"))
        cancelled = Cancelled(
            order_id=self.order_id,
            customer_id=self.customer_id,
            lines=self.lines,
            total=None,
            reason=reason.strip(),
            cancelled_at=at,
        )
        return Ok(Transition(cancelled, OrderCancelled(self.order_id, reason.strip(), at)))


@dataclass(frozen=True, slots=True)
class Placed:
    """Замовлення оформлене й очікує оплати."""

    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    total: Money
    delivery: DeliveryMethod
    payment: PaymentMethod
    placed_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.placed_at)

    def pay(
        self, receipt: PaymentReceipt, at: datetime
    ) -> Result[Transition[Paid, OrderPaid], DomainError]:
        """Підтверджує оплату: Placed -> Paid."""
        if receipt.amount != self.total:
            return Err(TotalMismatch(self.total, receipt.amount))
        if at < self.placed_at:
            return Err(InvalidValue("at", "час оплати не може бути раніше оформлення"))
        paid = Paid(
            order_id=self.order_id,
            customer_id=self.customer_id,
            lines=self.lines,
            total=self.total,
            delivery=self.delivery,
            payment=self.payment,
            placed_at=self.placed_at,
            payment_ref=receipt.transaction_id,
            paid_at=at,
        )
        return Ok(
            Transition(
                paid,
                OrderPaid(self.order_id, self.total, receipt.transaction_id, at),
            )
        )

    def cancel(self, reason: str, at: datetime) -> Result[Transition[Cancelled, OrderCancelled], DomainError]:
        """Скасовує неоплачене замовлення."""
        if not reason.strip():
            return Err(InvalidValue("reason", "причина скасування обов'язкова"))
        if at < self.placed_at:
            return Err(InvalidValue("at", "час скасування не може бути раніше оформлення"))
        cancelled = Cancelled(
            order_id=self.order_id,
            customer_id=self.customer_id,
            lines=self.lines,
            total=self.total,
            reason=reason.strip(),
            cancelled_at=at,
        )
        return Ok(Transition(cancelled, OrderCancelled(self.order_id, reason.strip(), at)))


@dataclass(frozen=True, slots=True)
class Paid:
    """Замовлення оплачене й очікує відправлення."""

    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    total: Money
    delivery: DeliveryMethod
    payment: PaymentMethod
    placed_at: datetime
    payment_ref: str
    paid_at: datetime

    def __post_init__(self) -> None:
        if not self.payment_ref.strip():
            raise ValueError("payment_ref не може бути порожнім")
        _require_aware(self.placed_at)
        _require_aware(self.paid_at)

    def ship(
        self, tracking: TrackingNumber, at: datetime
    ) -> Result[Transition[Shipped, OrderShipped], DomainError]:
        """Відправляє замовлення: Paid -> Shipped."""
        if at < self.paid_at:
            return Err(InvalidValue("at", "час відправлення не може бути раніше оплати"))
        shipped = Shipped(
            order_id=self.order_id,
            customer_id=self.customer_id,
            lines=self.lines,
            total=self.total,
            delivery=self.delivery,
            payment=self.payment,
            placed_at=self.placed_at,
            payment_ref=self.payment_ref,
            paid_at=self.paid_at,
            tracking=tracking,
            shipped_at=at,
        )
        return Ok(
            Transition(
                shipped,
                OrderShipped(self.order_id, tracking.value, at),
            )
        )


@dataclass(frozen=True, slots=True)
class Shipped:
    """Замовлення відправлене; це кінцевий стан."""

    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    total: Money
    delivery: DeliveryMethod
    payment: PaymentMethod
    placed_at: datetime
    payment_ref: str
    paid_at: datetime
    tracking: TrackingNumber
    shipped_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.placed_at)
        _require_aware(self.paid_at)
        _require_aware(self.shipped_at)


@dataclass(frozen=True, slots=True)
class Cancelled:
    """Замовлення скасоване; це кінцевий стан."""

    order_id: OrderId
    customer_id: CustomerId
    lines: tuple[OrderLine, ...]
    total: Money | None
    reason: str
    cancelled_at: datetime

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason не може бути порожнім")
        _require_aware(self.cancelled_at)


Order: TypeAlias = Draft | Placed | Paid | Shipped | Cancelled


def state_name(order: Order) -> str:
    """Назва стану для повідомлень і логів."""
    match order:
        case Draft():
            return "Draft"
        case Placed():
            return "Placed"
        case Paid():
            return "Paid"
        case Shipped():
            return "Shipped"
        case Cancelled():
            return "Cancelled"
        case _:
            assert_never(order)


def order_total(order: Order) -> Money | None:
    """Сума замовлення; у чернетки її ще немає."""
    match order:
        case Draft():
            return None
        case Placed(total=total) | Paid(total=total) | Shipped(total=total):
            return total
        case Cancelled(total=total):
            return total
        case _:
            assert_never(order)


def is_terminal(order: Order) -> bool:
    """Чи завершився життєвий цикл замовлення."""
    match order:
        case Shipped() | Cancelled():
            return True
        case Draft() | Placed() | Paid():
            return False
        case _:
            assert_never(order)
