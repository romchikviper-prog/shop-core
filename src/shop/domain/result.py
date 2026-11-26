"""Результат операції як алгебраїчний тип: Ok[T] або Err[E].

Очікувані помилки домену повертаються значенням, а не через винятки.
Обидва варіанти є незмінними, тому Result безпечно передавати між шарами.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar, assert_never

T_co = TypeVar("T_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)
T = TypeVar("T")
E = TypeVar("E")
U = TypeVar("U")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T_co]):
    """Успішний результат."""

    value: T_co


@dataclass(frozen=True, slots=True)
class Err(Generic[E_co]):
    """Очікувана помилка."""

    error: E_co


Result: TypeAlias = Ok[T_co] | Err[E_co]


def is_ok(result: Result[T, E]) -> bool:
    """Чи є результат успішним."""
    match result:
        case Ok():
            return True
        case Err():
            return False
        case _:
            assert_never(result)


def is_err(result: Result[T, E]) -> bool:
    """Чи є результат помилкою."""
    return not is_ok(result)


def map_result(result: Result[T, E], transform: Callable[[T], U]) -> Result[U, E]:
    """Перетворює успішне значення, не торкаючись помилки."""
    match result:
        case Ok(value):
            return Ok(transform(value))
        case Err(error):
            return Err(error)
        case _:
            assert_never(result)


def flat_map(result: Result[T, E], transform: Callable[[T], Result[U, E]]) -> Result[U, E]:
    """Ланцюжок операцій, кожна з яких може повернути помилку."""
    match result:
        case Ok(value):
            return transform(value)
        case Err(error):
            return Err(error)
        case _:
            assert_never(result)


def unwrap_or(result: Result[T, E], default: T) -> T:
    """Повертає значення успіху або заздалегідь задане значення."""
    match result:
        case Ok(value):
            return value
        case Err():
            return default
        case _:
            assert_never(result)
