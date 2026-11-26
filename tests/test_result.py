"""Поведінка узагальненого Result."""

from __future__ import annotations

from shop.domain.result import Err, Ok, Result, flat_map, is_err, is_ok, map_result, unwrap_or


def test_map_result_transforms_success() -> None:
    result: Result[int, str] = Ok(2)
    assert map_result(result, lambda value: value * 10) == Ok(20)


def test_map_result_keeps_error() -> None:
    result: Result[int, str] = Err("boom")
    assert map_result(result, lambda value: value * 10) == Err("boom")


def test_flat_map_chains_success() -> None:
    result: Result[int, str] = Ok(2)
    chained = flat_map(result, lambda value: Ok(value + 3))
    assert chained == Ok(5)


def test_flat_map_stops_on_first_error() -> None:
    result: Result[int, str] = Err("first")
    chained = flat_map(result, lambda value: Ok(value + 3))
    assert chained == Err("first")


def test_unwrap_or_returns_default_for_error() -> None:
    assert unwrap_or(Ok(5), 0) == 5
    assert unwrap_or(Err("boom"), 0) == 0


def test_is_ok_and_is_err() -> None:
    assert is_ok(Ok(1))
    assert not is_err(Ok(1))
    assert not is_ok(Err("boom"))
    assert is_err(Err("boom"))
