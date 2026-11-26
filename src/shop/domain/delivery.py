"""Способи доставки та пов'язані Value Object-и."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, TypeAlias, assert_never

from .errors import DomainError, InvalidValue
from .money import Money
from .result import Err, Ok, Result

_POSTAL_CODE = re.compile(r"^\d{5}$")
_STORE_ID = re.compile(r"^[A-Z]{2,10}-\d{3}$")
_TRACKING = re.compile(r"^[A-Z0-9-]{6,24}$")


@dataclass(frozen=True, slots=True)
class Address:
    """Адреса доставки."""

    city: str
    street: str
    building: str
    postal_code: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("city", self.city),
            ("street", self.street),
            ("building", self.building),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} не може бути порожнім")
        if not _POSTAL_CODE.fullmatch(self.postal_code):
            raise ValueError(f"некоректний поштовий індекс: {self.postal_code!r}")

    def __str__(self) -> str:
        return f"{self.city}, {self.street}, {self.building}, {self.postal_code}"


@dataclass(frozen=True, slots=True)
class StoreId:
    """Ідентифікатор пункту самовивозу."""

    value: str

    def __post_init__(self) -> None:
        if not _STORE_ID.fullmatch(self.value):
            raise ValueError(f"некоректний ідентифікатор магазину: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TrackingNumber:
    """Номер відправлення служби доставки."""

    value: str

    def __post_init__(self) -> None:
        if not _TRACKING.fullmatch(self.value):
            raise ValueError(f"некоректний номер відправлення: {self.value!r}")

    @classmethod
    def parse(cls, raw: str) -> Result[TrackingNumber, DomainError]:
        """Розбирає номер відправлення із зовнішнього джерела."""
        text = raw.strip().upper()
        if not _TRACKING.fullmatch(text):
            return Err(
                InvalidValue(
                    "tracking",
                    f"очікується 6–24 символи A-Z, 0-9 або '-', отримано {raw!r}",
                )
            )
        return Ok(cls(text))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CourierDelivery:
    """Кур'єрська доставка на адресу."""

    address: Address
    price: Money
    kind: ClassVar[str] = "courier"


@dataclass(frozen=True, slots=True)
class PickupDelivery:
    """Самовивіз із пункту видачі."""

    store: StoreId
    price: Money
    kind: ClassVar[str] = "pickup"


@dataclass(frozen=True, slots=True)
class ExpressDelivery:
    """Експрес-доставка на адресу."""

    address: Address
    price: Money
    kind: ClassVar[str] = "express"


DeliveryMethod: TypeAlias = CourierDelivery | PickupDelivery | ExpressDelivery


def delivery_label(method: DeliveryMethod) -> str:
    """Опис способу доставки для інтерфейсу."""
    match method:
        case CourierDelivery(address):
            return f"Кур'єр: {address}"
        case PickupDelivery(store):
            return f"Самовивіз: {store}"
        case ExpressDelivery(address):
            return f"Експрес: {address}"
        case _:
            assert_never(method)
