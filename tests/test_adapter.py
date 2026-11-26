"""Адаптер платіжного шлюзу: перетворення типів і помилок."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import pytest

from shop.application.ports import PaymentRequest
from shop.domain.errors import PaymentDeclined, PaymentUnavailable
from shop.domain.ids import OrderId
from shop.domain.result import Err, Ok
from shop.infrastructure.payment_adapter import LegacyPayAdapter, LegacyPayError
from tests.helpers import LATER, uah


class StubLegacyClient:
    def __init__(
        self,
        response: Mapping[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {}
        self.error = error
        self.calls: list[dict[str, int | str]] = []

    def authorize(
        self,
        *,
        amount_minor: int,
        currency: str,
        token: str,
        request_id: str,
    ) -> Mapping[str, str]:
        self.calls.append(
            {
                "amount_minor": amount_minor,
                "currency": currency,
                "token": token,
                "request_id": request_id,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def make_request(amount: str = "19999.99") -> PaymentRequest:
    return PaymentRequest(
        order_id=OrderId.new(),
        amount=uah(amount),
        token="card-token",
        idempotency_key="order:test:payment",
    )


def test_adapter_maps_approved_response() -> None:
    client = StubLegacyClient({"status": "approved", "transaction_id": "tx-42"})
    adapter = LegacyPayAdapter(client, FixedClock(LATER))
    result = adapter.charge(make_request())
    assert isinstance(result, Ok)
    assert result.value.transaction_id == "tx-42"
    assert result.value.amount == uah("19999.99")
    assert result.value.charged_at == LATER


def test_adapter_converts_amount_to_minor_units() -> None:
    client = StubLegacyClient({"status": "approved", "transaction_id": "tx-42"})
    adapter = LegacyPayAdapter(client, FixedClock(LATER))
    adapter.charge(make_request("19999.99"))
    assert client.calls[0]["amount_minor"] == 1999999
    assert client.calls[0]["currency"] == "UAH"


def test_adapter_forwards_idempotency_key_and_token() -> None:
    client = StubLegacyClient({"status": "approved", "transaction_id": "tx-42"})
    adapter = LegacyPayAdapter(client, FixedClock(LATER))
    adapter.charge(make_request())
    assert client.calls[0]["request_id"] == "order:test:payment"
    assert client.calls[0]["token"] == "card-token"


def test_adapter_maps_declined_response() -> None:
    client = StubLegacyClient({"status": "declined", "reason": "недостатньо коштів"})
    adapter = LegacyPayAdapter(client, FixedClock(LATER))
    result = adapter.charge(make_request())
    assert isinstance(result, Err)
    assert isinstance(result.error, PaymentDeclined)
    assert result.error.reason == "недостатньо коштів"


def test_adapter_maps_sdk_exception() -> None:
    client = StubLegacyClient(error=LegacyPayError("timeout"))
    adapter = LegacyPayAdapter(client, FixedClock(LATER))
    result = adapter.charge(make_request())
    assert isinstance(result, Err)
    assert isinstance(result.error, PaymentUnavailable)
    assert "timeout" in result.error.reason


@pytest.mark.parametrize(
    "response",
    [
        {"status": "approved"},
        {"status": "unknown"},
        {},
    ],
)
def test_adapter_rejects_incomplete_or_unknown_response(response: dict[str, str]) -> None:
    client = StubLegacyClient(response)
    adapter = LegacyPayAdapter(client, FixedClock(LATER))
    result = adapter.charge(make_request())
    assert isinstance(result, Err)
    assert isinstance(result.error, PaymentUnavailable)
