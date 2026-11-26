"""Способи оплати та підтвердження платежу."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, TypeAlias, assert_never

from .money import Money


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    """Підтвердження успішного списання від платіжного шлюзу."""

    transaction_id: str
    amount: Money
    charged_at: datetime

    def __post_init__(self) -> None:
        if not self.transaction_id.strip():
            raise ValueError("transaction_id не може бути порожнім")
        if self.charged_at.tzinfo is None:
            raise ValueError("charged_at має бути з часовою зоною")


@dataclass(frozen=True, slots=True)
class CardPayment:
    """Оплата карткою через платіжний шлюз."""

    provider: str = "acme-pay"
    kind: ClassVar[str] = "card"

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider не може бути порожнім")


@dataclass(frozen=True, slots=True)
class BankTransferPayment:
    """Оплата банківським переказом через платіжний шлюз."""

    bank_code: str = "bank-transfer"
    kind: ClassVar[str] = "bank_transfer"

    def __post_init__(self) -> None:
        if not self.bank_code.strip():
            raise ValueError("bank_code не може бути порожнім")


PaymentMethod: TypeAlias = CardPayment | BankTransferPayment


def payment_label(method: PaymentMethod) -> str:
    """Опис способу оплати для інтерфейсу."""
    match method:
        case CardPayment(provider):
            return f"Картка ({provider})"
        case BankTransferPayment(bank_code):
            return f"Банківський переказ ({bank_code})"
        case _:
            assert_never(method)
