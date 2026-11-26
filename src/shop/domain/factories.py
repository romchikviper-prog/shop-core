"""Фабрики способів доставки й оплати.

Патерн Factory Method: клієнт працює з інтерфейсом фабрики, а конкретна
фабрика вирішує, який тип способу доставки або оплати створити. Коди з
зовнішнього API перетворюються на фабрики в одному місці, тому новий
спосіб не розкидає if/elif по сервісу замовлень.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, assert_never

from .delivery import Address, CourierDelivery, DeliveryMethod, ExpressDelivery, PickupDelivery, StoreId
from .errors import DomainError, InvalidValue, UnknownDeliveryMethod, UnknownPaymentMethod
from .money import Currency, Money
from .payment import BankTransferPayment, CardPayment, PaymentMethod
from .result import Err, Ok, Result

COURIER_PRICE = Money(Decimal("99.00"), Currency.UAH)
EXPRESS_PRICE = Money(Decimal("199.00"), Currency.UAH)


class DeliveryMethodFactory(Protocol):
    """Інтерфейс фабрики способу доставки."""

    def create(self) -> DeliveryMethod: ...


@dataclass(frozen=True, slots=True)
class CourierDeliveryFactory:
    """Створює кур'єрську доставку на задану адресу."""

    address: Address
    price: Money = COURIER_PRICE

    def create(self) -> DeliveryMethod:
        return CourierDelivery(self.address, self.price)


@dataclass(frozen=True, slots=True)
class PickupDeliveryFactory:
    """Створює самовивіз із пункту видачі."""

    store: StoreId
    price: Money = Money.zero(Currency.UAH)

    def create(self) -> DeliveryMethod:
        return PickupDelivery(self.store, self.price)


@dataclass(frozen=True, slots=True)
class ExpressDeliveryFactory:
    """Створює експрес-доставку на задану адресу."""

    address: Address
    price: Money = EXPRESS_PRICE

    def create(self) -> DeliveryMethod:
        return ExpressDelivery(self.address, self.price)


def delivery_factory(
    code: str,
    *,
    address: Address | None = None,
    store: StoreId | None = None,
) -> Result[DeliveryMethodFactory, DomainError]:
    """Повертає фабрику за кодом способу доставки."""
    match code.strip().lower():
        case "courier":
            if address is None:
                return Err(InvalidValue("address", "для кур'єрської доставки потрібна адреса"))
            return Ok(CourierDeliveryFactory(address))
        case "pickup":
            if store is None:
                return Err(InvalidValue("store", "для самовивозу потрібен пункт видачі"))
            return Ok(PickupDeliveryFactory(store))
        case "express":
            if address is None:
                return Err(InvalidValue("address", "для експрес-доставки потрібна адреса"))
            return Ok(ExpressDeliveryFactory(address))
        case _:
            return Err(UnknownDeliveryMethod(code))


class PaymentMethodFactory(Protocol):
    """Інтерфейс фабрики способу оплати."""

    def create(self) -> PaymentMethod: ...


@dataclass(frozen=True, slots=True)
class CardPaymentFactory:
    """Створює оплату карткою."""

    provider: str = "acme-pay"

    def create(self) -> PaymentMethod:
        return CardPayment(self.provider)


@dataclass(frozen=True, slots=True)
class BankTransferPaymentFactory:
    """Створює оплату банківським переказом."""

    bank_code: str = "bank-transfer"

    def create(self) -> PaymentMethod:
        return BankTransferPayment(self.bank_code)


def payment_factory(code: str) -> Result[PaymentMethodFactory, DomainError]:
    """Повертає фабрику за кодом способу оплати."""
    match code.strip().lower():
        case "card":
            return Ok(CardPaymentFactory())
        case "bank_transfer":
            return Ok(BankTransferPaymentFactory())
        case _:
            return Err(UnknownPaymentMethod(code))


def make_delivery(
    code: str,
    *,
    address: Address | None = None,
    store: StoreId | None = None,
) -> Result[DeliveryMethod, DomainError]:
    """Створює готовий спосіб доставки за кодом."""
    factory_result = delivery_factory(code, address=address, store=store)
    match factory_result:
        case Ok(factory):
            return Ok(factory.create())
        case Err(error):
            return Err(error)
        case _:
            assert_never(factory_result)


def make_payment(code: str) -> Result[PaymentMethod, DomainError]:
    """Створює готовий спосіб оплати за кодом."""
    factory_result = payment_factory(code)
    match factory_result:
        case Ok(factory):
            return Ok(factory.create())
        case Err(error):
            return Err(error)
        case _:
            assert_never(factory_result)
