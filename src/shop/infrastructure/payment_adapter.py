"""Адаптер стороннього платіжного SDK до порту PaymentGateway.

Патерн Adapter: зовнішній клієнт говорить у мінорних одиницях, рядковими
статусами та власними винятками. Ядро бачить лише PaymentRequest і
Result[PaymentReceipt, DomainError], тому заміна шлюзу не зачіпає домен.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from shop.application.ports import Clock, PaymentGateway, PaymentRequest
from shop.domain.errors import DomainError, PaymentDeclined, PaymentUnavailable
from shop.domain.payment import PaymentReceipt
from shop.domain.result import Err, Ok, Result


class LegacyPayError(Exception):
    """Помилка стороннього платіжного SDK."""


class LegacyPayClient(Protocol):
    """Мінімальний інтерфейс стороннього SDK (не наш тип)."""

    def authorize(
        self,
        *,
        amount_minor: int,
        currency: str,
        token: str,
        request_id: str,
    ) -> Mapping[str, str]: ...


class LegacyPayAdapter(PaymentGateway):
    """Перетворює наш порт на виклики LegacyPayClient."""

    def __init__(self, client: LegacyPayClient, clock: Clock) -> None:
        self._client = client
        self._clock = clock

    def charge(self, request: PaymentRequest) -> Result[PaymentReceipt, DomainError]:
        amount_minor = int((request.amount.amount * 100).to_integral_value())
        try:
            response = self._client.authorize(
                amount_minor=amount_minor,
                currency=request.amount.currency.value,
                token=request.token,
                request_id=request.idempotency_key,
            )
        except LegacyPayError as error:
            return Err(PaymentUnavailable(str(error)))

        status = response.get("status")
        match status:
            case "approved":
                transaction_id = response.get("transaction_id", "").strip()
                if not transaction_id:
                    return Err(PaymentUnavailable("шлюз не повернув transaction_id"))
                return Ok(
                    PaymentReceipt(
                        transaction_id=transaction_id,
                        amount=request.amount,
                        charged_at=self._clock.now(),
                    )
                )
            case "declined":
                return Err(PaymentDeclined(response.get("reason", "без причини")))
            case _:
                return Err(PaymentUnavailable(f"неочікуваний статус: {status!r}"))


class SandboxLegacyPayClient:
    """Пісочниця для демонстраційного запуску; не є частиною домену."""

    def __init__(self) -> None:
        self._charges: dict[str, dict[str, str]] = {}
        self._next_transaction = 0

    def authorize(
        self,
        *,
        amount_minor: int,
        currency: str,
        token: str,
        request_id: str,
    ) -> Mapping[str, str]:
        existing = self._charges.get(request_id)
        if existing is not None:
            return existing
        if token.startswith("fail"):
            raise LegacyPayError("тайм-аут пісочниці")
        if token.startswith("decline"):
            response = {
                "status": "declined",
                "reason": "недостатньо коштів",
                "amount_minor": str(amount_minor),
                "currency": currency,
            }
        else:
            self._next_transaction += 1
            response = {
                "status": "approved",
                "transaction_id": f"tx-{self._next_transaction:04d}",
                "amount_minor": str(amount_minor),
                "currency": currency,
            }
        self._charges[request_id] = response
        return response
