"""Тестові дублери портів."""

from __future__ import annotations

from datetime import datetime

from shop.application.ports import PaymentRequest
from shop.domain.errors import DomainError, PaymentDeclined
from shop.domain.payment import PaymentReceipt
from shop.domain.result import Err, Ok, Result
from tests.helpers import NOW


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class ApprovingGateway:
    def __init__(self) -> None:
        self.requests: list[PaymentRequest] = []

    def charge(self, request: PaymentRequest) -> Result[PaymentReceipt, DomainError]:
        self.requests.append(request)
        return Ok(
            PaymentReceipt(
                transaction_id=f"tx-{len(self.requests):04d}",
                amount=request.amount,
                charged_at=NOW,
            )
        )


class DecliningGateway:
    def __init__(self, reason: str = "недостатньо коштів") -> None:
        self.reason = reason
        self.requests: list[PaymentRequest] = []

    def charge(self, request: PaymentRequest) -> Result[PaymentReceipt, DomainError]:
        self.requests.append(request)
        return Err(PaymentDeclined(self.reason))


class SequenceGateway:
    """Повертає заздалегідь задані результати по черзі."""

    def __init__(self, results: list[Result[PaymentReceipt, DomainError]]) -> None:
        self._results = list(results)
        self.requests: list[PaymentRequest] = []

    def charge(self, request: PaymentRequest) -> Result[PaymentReceipt, DomainError]:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("немає заготовленої відповіді шлюзу")
        return self._results.pop(0)
