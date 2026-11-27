"""HTTP-сервіс розрахунку ціни замовлення (ЛР5, варіант 2)."""

from shop.api.config import Settings
from shop.api.service import RequestError, evaluate
from shop.api.version import GIT_SHA, VERSION

__all__ = ["GIT_SHA", "VERSION", "RequestError", "Settings", "evaluate"]
