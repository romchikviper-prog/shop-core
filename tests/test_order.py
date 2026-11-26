"""Життєвий цикл агрегату «Замовлення» та недоступні переходи."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from shop.domain.delivery import TrackingNumber
from shop.domain.errors import CurrencyMismatch, EmptyOrder, InvalidValue, TotalMismatch
from shop.domain.ids import Sku
from shop.domain.money import Currency, Money
from shop.domain.order import (
    Cancelled,
    Draft,
    OrderLine,
    Paid,
    Placed,
    Shipped,
    is_terminal,
    order_total,
    state_name,
)
from shop.domain.payment import CardPayment, PaymentReceipt
from shop.domain.result import Err, Ok
from tests.helpers import (
    LATER,
    NOW,
    default_delivery,
    laptop_line,
    make_draft,
    make_paid,
    make_placed,
    make_shipped,
    phone_line,
    uah,
)


def test_draft_exposes_only_draft_transitions() -> None:
    draft = make_draft()
    assert hasattr(draft, "add_line")
    assert hasattr(draft, "place")
    assert hasattr(draft, "cancel")
    assert not hasattr(draft, "pay")
    assert not hasattr(draft, "ship")


def test_placed_cannot_ship_before_payment() -> None:
    placed = make_placed()
    assert hasattr(placed, "pay")
    assert hasattr(placed, "cancel")
    assert not hasattr(placed, "ship")


def test_paid_cannot_be_cancelled() -> None:
    paid = make_paid()
    assert hasattr(paid, "ship")
    assert not hasattr(paid, "cancel")


def test_shipped_and_cancelled_are_terminal() -> None:
    shipped = make_shipped()
    cancelled = make_draft().cancel("не потрібно", LATER)
    assert isinstance(cancelled, Ok)
    assert is_terminal(shipped)
    assert is_terminal(cancelled.value.state)
    for order in (shipped, cancelled.value.state):
        assert not hasattr(order, "pay")
        assert not hasattr(order, "ship")
        assert not hasattr(order, "cancel")


def test_full_happy_path_draft_placed_paid_shipped() -> None:
    draft = make_draft(lines=(phone_line(2), laptop_line()))
    assert isinstance(draft, Draft)

    placed = make_placed(draft)
    assert isinstance(placed, Placed)
    assert placed.total == uah("85997.00").plus(uah("99.00"))

    receipt = PaymentReceipt("tx-1", placed.total, LATER)
    pay_result = placed.pay(receipt, LATER)
    assert isinstance(pay_result, Ok)
    paid = pay_result.value.state
    assert isinstance(paid, Paid)
    assert paid.payment_ref == "tx-1"
    assert pay_result.value.event.transaction_id == "tx-1"

    ship_result = paid.ship(TrackingNumber("NP-123456789"), LATER + timedelta(minutes=1))
    assert isinstance(ship_result, Ok)
    shipped = ship_result.value.state
    assert isinstance(shipped, Shipped)
    assert shipped.tracking.value == "NP-123456789"
    assert order_total(shipped) == placed.total


def test_add_line_merges_same_sku() -> None:
    draft = make_draft(lines=(phone_line(1),))
    result = draft.add_line(phone_line(2))
    assert isinstance(result, Ok)
    assert len(result.value.lines) == 1
    assert result.value.lines[0].quantity == 3
    assert result.value.lines[0].total == uah("59997.00")


def test_add_line_rejects_currency_mismatch() -> None:
    draft = make_draft(lines=(phone_line(),))
    foreign = OrderLine(
        sku=Sku("HDP-0003"),
        name="Навушники",
        unit_price=Money(Decimal("100.00"), Currency.USD),
        quantity=1,
    )
    result = draft.add_line(foreign)
    assert isinstance(result, Err)
    assert isinstance(result.error, CurrencyMismatch)


def test_remove_line_missing_sku_returns_error() -> None:
    draft = make_draft(lines=(phone_line(),))
    result = draft.remove_line(Sku("HDP-0003"))
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "sku"


def test_place_empty_order_returns_error() -> None:
    draft = make_draft(lines=())
    result = draft.place(uah("100.00"), default_delivery(), CardPayment(), LATER)
    assert isinstance(result, Err)
    assert isinstance(result.error, EmptyOrder)


def test_place_requires_positive_total() -> None:
    draft = make_draft()
    result = draft.place(uah("0.00"), default_delivery(), CardPayment(), LATER)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)
    assert result.error.field == "total"


def test_place_rejects_total_in_other_currency() -> None:
    draft = make_draft()
    result = draft.place(
        Money(Decimal("100.00"), Currency.USD),
        default_delivery(),
        CardPayment(),
        LATER,
    )
    assert isinstance(result, Err)
    assert isinstance(result.error, CurrencyMismatch)


def test_place_rejects_time_before_creation() -> None:
    draft = make_draft()
    result = draft.place(uah("20000.00"), default_delivery(), CardPayment(), NOW - timedelta(seconds=1))
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


def test_pay_rejects_amount_mismatch() -> None:
    placed = make_placed()
    result = placed.pay(PaymentReceipt("tx-1", uah("1.00"), LATER), LATER)
    assert isinstance(result, Err)
    assert isinstance(result.error, TotalMismatch)


def test_pay_rejects_time_before_placement() -> None:
    placed = make_placed()
    result = placed.pay(PaymentReceipt("tx-1", placed.total, NOW), NOW)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


def test_ship_rejects_time_before_payment() -> None:
    paid = make_paid()
    result = paid.ship(TrackingNumber("NP-123456789"), NOW)
    assert isinstance(result, Err)
    assert isinstance(result.error, InvalidValue)


@pytest.mark.parametrize("reason", ["", "   "])
def test_cancel_requires_reason(reason: str) -> None:
    draft_result = make_draft().cancel(reason, LATER)
    placed_result = make_placed().cancel(reason, LATER)
    assert isinstance(draft_result, Err)
    assert isinstance(placed_result, Err)
    assert isinstance(draft_result.error, InvalidValue)
    assert isinstance(placed_result.error, InvalidValue)


def test_cancel_placed_keeps_total_and_reason() -> None:
    placed = make_placed()
    result = placed.cancel("клієнт передумав", LATER)
    assert isinstance(result, Ok)
    cancelled = result.value.state
    assert isinstance(cancelled, Cancelled)
    assert cancelled.total == placed.total
    assert cancelled.reason == "клієнт передумав"
    assert result.value.event.reason == "клієнт передумав"


def test_cancelled_draft_has_no_total() -> None:
    result = make_draft().cancel("не потрібно", LATER)
    assert isinstance(result, Ok)
    assert result.value.state.total is None


def test_state_name_for_all_states() -> None:
    draft = make_draft()
    placed = make_placed()
    paid = make_paid()
    shipped = make_shipped()
    cancelled = make_draft().cancel("не потрібно", LATER)
    assert isinstance(cancelled, Ok)
    assert state_name(draft) == "Draft"
    assert state_name(placed) == "Placed"
    assert state_name(paid) == "Paid"
    assert state_name(shipped) == "Shipped"
    assert state_name(cancelled.value.state) == "Cancelled"


def test_order_total_is_none_only_for_draft() -> None:
    assert order_total(make_draft()) is None
    assert order_total(make_placed()) == uah("20098.00")
    assert order_total(make_paid()) == uah("20098.00")
