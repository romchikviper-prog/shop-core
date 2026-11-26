"""Сервіс застосунку: сценарії замовлення, оплати й доставки.

Залежності передаються лише через конструктор. Сервіс не створює ні
репозиторій, ні платіжний шлюз, ні калькулятор цін — їх підставляє корінь
композиції (demo.py або тест).
"""

from __future__ import annotations

from typing import assert_never

from shop.application.ports import (
    Clock,
    InventoryPort,
    OrderRepository,
    PaymentGateway,
    PaymentRequest,
)
from shop.domain.commands import (
    AddItem,
    CancelOrder,
    Command,
    CreateOrder,
    PayOrder,
    PlaceOrder,
    RemoveItem,
    ShipOrder,
)
from shop.domain.delivery import TrackingNumber
from shop.domain.errors import DomainError, InvalidState, InvalidValue, OrderNotFound
from shop.domain.events import OrderCancelled, OrderCreated, OrderPaid, OrderPlaced, OrderShipped
from shop.domain.factories import make_delivery, make_payment
from shop.domain.ids import OrderId
from shop.domain.inventory import StockRequest
from shop.domain.order import Draft, Order, OrderLine, Paid, Placed, state_name
from shop.domain.pricing import PriceCalculator
from shop.domain.result import Err, Ok, Result


class OrderService:
    """Оркеструє переходи агрегату та взаємодію з портами."""

    def __init__(
        self,
        orders: OrderRepository,
        inventory: InventoryPort,
        payments: PaymentGateway,
        pricing: PriceCalculator,
        clock: Clock,
    ) -> None:
        self._orders = orders
        self._inventory = inventory
        self._payments = payments
        self._pricing = pricing
        self._clock = clock

    def create_order(self, command: CreateOrder) -> Result[OrderCreated, DomainError]:
        """Створює чернетку замовлення."""
        now = self._clock.now()
        draft = Draft(
            order_id=OrderId.new(),
            customer_id=command.customer_id,
            lines=(),
            created_at=now,
        )
        self._orders.save(draft)
        return Ok(OrderCreated(draft.order_id, command.customer_id, now))

    def add_item(self, command: AddItem) -> Result[Order, DomainError]:
        """Додає товар у чернетку."""
        order = self._orders.get(command.order_id)
        if order is None:
            return Err(OrderNotFound(command.order_id))
        if not isinstance(order, Draft):
            return Err(InvalidState("Draft", state_name(order)))
        try:
            line = OrderLine(
                sku=command.sku,
                name=command.name,
                unit_price=command.unit_price,
                quantity=command.quantity,
            )
        except ValueError as exc:
            return Err(InvalidValue("item", str(exc)))
        match order.add_line(line):
            case Err(error):
                return Err(error)
            case Ok(updated):
                self._orders.save(updated)
                return Ok(updated)

    def remove_item(self, command: RemoveItem) -> Result[Order, DomainError]:
        """Прибирає товар із чернетки."""
        order = self._orders.get(command.order_id)
        if order is None:
            return Err(OrderNotFound(command.order_id))
        if not isinstance(order, Draft):
            return Err(InvalidState("Draft", state_name(order)))
        match order.remove_line(command.sku):
            case Err(error):
                return Err(error)
            case Ok(updated):
                self._orders.save(updated)
                return Ok(updated)

    def place_order(self, command: PlaceOrder) -> Result[OrderPlaced, DomainError]:
        """Оформлює замовлення, резервуючи товар на складі."""
        order = self._orders.get(command.order_id)
        if order is None:
            return Err(OrderNotFound(command.order_id))
        if not isinstance(order, Draft):
            return Err(InvalidState("Draft", state_name(order)))

        delivery_result = make_delivery(
            command.delivery_code,
            address=command.address,
            store=command.store,
        )
        if isinstance(delivery_result, Err):
            return Err(delivery_result.error)
        delivery = delivery_result.value

        payment_result = make_payment(command.payment_code)
        if isinstance(payment_result, Err):
            return Err(payment_result.error)
        payment = payment_result.value

        total = self._pricing.total(order.lines).plus(delivery.price)
        requests = tuple(StockRequest(line.sku, line.quantity) for line in order.lines)
        reservation = self._inventory.reserve(order.order_id, requests)
        if isinstance(reservation, Err):
            return Err(reservation.error)

        match order.place(total, delivery, payment, self._clock.now()):
            case Err(error):
                self._inventory.release(order.order_id)
                return Err(error)
            case Ok(transition):
                self._orders.save(transition.state)
                return Ok(transition.event)

    def pay_order(self, command: PayOrder) -> Result[OrderPaid, DomainError]:
        """Проводить оплату через шлюз і переводить замовлення в Paid."""
        order = self._orders.get(command.order_id)
        if order is None:
            return Err(OrderNotFound(command.order_id))
        if not isinstance(order, Placed):
            return Err(InvalidState("Placed", state_name(order)))
        if not command.token.strip():
            return Err(InvalidValue("token", "токен оплати обов'язковий"))

        request = PaymentRequest(
            order_id=order.order_id,
            amount=order.total,
            token=command.token,
            idempotency_key=f"order:{order.order_id}:payment",
        )
        charge_result = self._payments.charge(request)
        if isinstance(charge_result, Err):
            return Err(charge_result.error)

        match order.pay(charge_result.value, self._clock.now()):
            case Err(error):
                return Err(error)
            case Ok(transition):
                self._orders.save(transition.state)
                return Ok(transition.event)

    def ship_order(self, command: ShipOrder) -> Result[OrderShipped, DomainError]:
        """Відправляє оплачене замовлення."""
        order = self._orders.get(command.order_id)
        if order is None:
            return Err(OrderNotFound(command.order_id))
        if not isinstance(order, Paid):
            return Err(InvalidState("Paid", state_name(order)))

        tracking_result = TrackingNumber.parse(command.tracking)
        if isinstance(tracking_result, Err):
            return Err(tracking_result.error)

        match order.ship(tracking_result.value, self._clock.now()):
            case Err(error):
                return Err(error)
            case Ok(transition):
                self._orders.save(transition.state)
                return Ok(transition.event)

    def cancel_order(self, command: CancelOrder) -> Result[OrderCancelled, DomainError]:
        """Скасовує чернетку або неоплачене замовлення."""
        order = self._orders.get(command.order_id)
        if order is None:
            return Err(OrderNotFound(command.order_id))

        if isinstance(order, Draft):
            match order.cancel(command.reason, self._clock.now()):
                case Err(error):
                    return Err(error)
                case Ok(transition):
                    self._orders.save(transition.state)
                    return Ok(transition.event)

        if isinstance(order, Placed):
            match order.cancel(command.reason, self._clock.now()):
                case Err(error):
                    return Err(error)
                case Ok(transition):
                    self._orders.save(transition.state)
                    self._inventory.release(order.order_id)
                    return Ok(transition.event)

        return Err(InvalidState("Draft або Placed", state_name(order)))

    def get_order(self, order_id: OrderId) -> Order | None:
        """Повертає замовлення для запитів і тестів."""
        return self._orders.get(order_id)

    def handle(self, command: Command) -> Result[object, DomainError]:
        """Єдина точка входу: диспетчеризація закритої множини команд."""
        match command:
            case CreateOrder():
                return self.create_order(command)
            case AddItem():
                return self.add_item(command)
            case RemoveItem():
                return self.remove_item(command)
            case PlaceOrder():
                return self.place_order(command)
            case PayOrder():
                return self.pay_order(command)
            case ShipOrder():
                return self.ship_order(command)
            case CancelOrder():
                return self.cancel_order(command)
            case _:
                assert_never(command)
