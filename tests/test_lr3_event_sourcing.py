"""Тести журналу подій, оптимістичного блокування та проєкції."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from shop.distributed.event_store import EventStore, VersionConflict
from shop.distributed.model import (
    OrderPlaced,
    PaymentCaptured,
    event_type_name,
)
from shop.distributed.projection import Projection, ProjectionStore, Projector
from shop.domain.ids import CustomerId, OrderId, Sku
from shop.domain.money import Currency, Money
from shop.domain.order import OrderLine


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def make_order_line() -> OrderLine:
    return OrderLine(
        sku=Sku("PHN-0001"),
        name="Смартфон Galaxy X",
        unit_price=Money(Decimal("19999.00"), Currency.UAH),
        quantity=1,
    )


def make_placed(order_id: OrderId, customer_id: CustomerId) -> OrderPlaced:
    line = make_order_line()
    return OrderPlaced(
        order_id=order_id,
        customer_id=customer_id,
        lines=(line,),
        total=line.total,
        at=NOW,
    )


def make_captured(order_id: OrderId, total: Money) -> PaymentCaptured:
    return PaymentCaptured(
        order_id=order_id,
        amount=total,
        transaction_id="pay-000001",
        at=NOW,
    )


def test_replay_restores_projection_and_checkpoint(tmp_path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    order_id = OrderId.new()
    customer_id = CustomerId.new()
    line = make_order_line()

    store.append(
        command_id="cmd-1",
        stream_id=str(order_id),
        expected_version=0,
        events=(make_placed(order_id, customer_id),),
    )
    store.append(
        command_id="cmd-2",
        stream_id=str(order_id),
        expected_version=1,
        events=(make_captured(order_id, line.total),),
    )

    first = Projection()
    first.rebuild(store.read_all())
    snapshot = first.snapshot()
    assert snapshot.checkpoint == 2
    assert first.get_order(str(order_id)).status == "paid"

    second = Projection()
    second.rebuild(store.read_all())
    assert second.snapshot() == snapshot
    assert second.checksum() == first.checksum()

    partial = Projection()
    partial.rebuild(store.read_all()[:1])
    projection_file = tmp_path / "projection.json"
    ProjectionStore(projection_file).save(partial.snapshot())
    loaded = ProjectionStore(projection_file).load()
    assert loaded is not None
    resumed = Projection()
    resumed.load(loaded)
    for stored in store.read_from(resumed.checkpoint):
        resumed.apply(stored)
    assert resumed.snapshot() == snapshot


def test_version_conflict_is_rejected(tmp_path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    order_id = OrderId.new()
    customer_id = CustomerId.new()
    line = make_order_line()

    store.append(
        command_id="cmd-1",
        stream_id=str(order_id),
        expected_version=0,
        events=(make_placed(order_id, customer_id),),
    )
    with pytest.raises(VersionConflict) as error:
        store.append(
            command_id="cmd-2",
            stream_id=str(order_id),
            expected_version=0,
            events=(make_captured(order_id, line.total),),
        )
    assert error.value.expected == 0
    assert error.value.actual == 1
    assert store.head() == 1
    state = store.rebuild_state(str(order_id))
    assert state.status == "placed"
    assert state.version == 1


def test_duplicate_command_does_not_change_state(tmp_path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    order_id = OrderId.new()
    customer_id = CustomerId.new()

    first = store.append(
        command_id="cmd-1",
        stream_id=str(order_id),
        expected_version=0,
        events=(make_placed(order_id, customer_id),),
    )
    second = store.append(
        command_id="cmd-1",
        stream_id=str(order_id),
        expected_version=999,
        events=(make_placed(order_id, customer_id),),
    )

    assert not first.duplicate
    assert second.duplicate
    assert second.stored == first.stored
    assert store.head() == 1
    assert store.rebuild_state(str(order_id)).version == 1


def test_event_store_survives_restart(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    order_id = OrderId.new()
    customer_id = CustomerId.new()
    store = EventStore(path)
    store.append(
        command_id="cmd-1",
        stream_id=str(order_id),
        expected_version=0,
        events=(make_placed(order_id, customer_id),),
    )

    reopened = EventStore(path)
    assert reopened.head() == 1
    state = reopened.rebuild_state(str(order_id))
    assert state.status == "placed"
    assert reopened.find_command("cmd-1") is not None
    assert event_type_name(make_placed(order_id, customer_id)) == "OrderPlaced"


def test_projection_lag_and_catch_up(tmp_path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    order_id = OrderId.new()
    customer_id = CustomerId.new()
    store.append(
        command_id="cmd-1",
        stream_id=str(order_id),
        expected_version=0,
        events=(make_placed(order_id, customer_id),),
    )

    projection = Projection()
    projector = Projector(
        store=store,
        projection=projection,
        snapshot_store=ProjectionStore(tmp_path / "projection.json"),
        poll_interval=0.01,
    )
    assert projector.lag().lag_events == 1
    projector.catch_up()
    lag = projector.lag()
    assert lag.lag_events == 0
    assert lag.checkpoint == 1
    assert lag.lag_ms == 0.0
