"""Спільні фікстури тестів."""

from __future__ import annotations

import pytest

from shop.application.order_service import OrderService
from shop.domain.money import Currency
from shop.domain.pricing import BasePrice
from shop.infrastructure.memory import InMemoryInventory, InMemoryOrderRepository
from tests.fakes import ApprovingGateway, FixedClock
from tests.helpers import LAPTOP, PHONE


@pytest.fixture
def orders() -> InMemoryOrderRepository:
    return InMemoryOrderRepository()


@pytest.fixture
def inventory() -> InMemoryInventory:
    return InMemoryInventory({PHONE: 5, LAPTOP: 2})


@pytest.fixture
def gateway() -> ApprovingGateway:
    return ApprovingGateway()


@pytest.fixture
def service(
    orders: InMemoryOrderRepository,
    inventory: InMemoryInventory,
    gateway: ApprovingGateway,
) -> OrderService:
    return OrderService(
        orders=orders,
        inventory=inventory,
        payments=gateway,
        pricing=BasePrice(Currency.UAH),
        clock=FixedClock(),
    )
