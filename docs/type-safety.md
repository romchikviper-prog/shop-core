# Докази типобезпеки

`mypy --strict` входить у команду `py -3 check.py` і перевіряє весь `src/`. Нижче — навмисно некоректні приклади з `docs/type-safety/`. Вони **не входять** у `src/` і не запускаються; їх єдине призначення — показати, що модель відхиляє помилки на етапі перевірки типів.

## 1. Недопустимий перехід стану

`docs/type-safety/invalid_transition.py`:

```python
draft = Draft(OrderId.new(), CustomerId.new(), (), datetime.now(UTC))
draft.ship("NP-123456789")
```

`mypy --strict`:

```text
docs/type-safety/invalid_transition.py:13: error: "Draft" has no attribute "ship"  [attr-defined]
```

Метод `ship` існує лише в стані `Paid`. Компілятор/аналізатор не дає навіть написати відправлення неоплаченого замовлення.

## 2. Неповний pattern matching

`docs/type-safety/incomplete_match.py`:

```python
def label(order: Order) -> str:
    match order:
        case Draft():
            return "draft"
        case _:
            assert_never(order)
```

`mypy --strict`:

```text
docs/type-safety/incomplete_match.py:17: error: Argument 1 to "assert_never" has incompatible type "Placed | Paid | Shipped | Cancelled"; expected "Never"  [arg-type]
```

`assert_never` робить вичерпність обов'язковою: якщо додати новий стан і не обробити його, перевірка типів упаде.

## 3. Некоректний тип валюти

`docs/type-safety/wrong_currency.py`:

```python
money = Money(Decimal("10.00"), "UAH")
```

`mypy --strict`:

```text
docs/type-safety/wrong_currency.py:11: error: Argument 2 to "Money" has incompatible type "str"; expected "Currency"  [arg-type]
```

Валюта — не рядок, а `Currency`; випадково передати `"UAH"` замість `Currency.UAH` неможливо.

## 4. Що проходить перевірку

```text
$ py -3 -m mypy --strict src
Success: no issues found in 22 source files
```

## 5. Механізми моделі

| Механізм | Де | Що гарантує |
|---|---|---|
| `@dataclass(frozen=True, slots=True)` | `Money`, `Sku`, `OrderId`, `OrderLine`, стани | Неможливо змінити поле після створення |
| `__post_init__` | `Money`, `OrderLine`, `StockItem`, `Address` | Інваріанти перевіряються в конструкторі |
| `Result = Ok[T] \| Err[E]` | `domain/result.py` | Очікувані помилки — значення, а не винятки |
| `Draft \| Placed \| Paid \| Shipped \| Cancelled` | `domain/order.py` | Кожен стан має лише дозволені переходи |
| `assert_never` | `state_name`, `describe_error`, `handle` | Вичерпність обробки перевіряє mypy |
| `Protocol` | `PriceCalculator`, `PaymentGateway`, `InventoryPort` | Залежність від інтерфейсу, а не від реалізації |
| `Generic` | `Result[T, E]`, `Transition[S, E]`, `Repository[A, Id]` | Повторне використання без втрати типів |

## 6. Відтворення

```bash
py -3 -m mypy --strict docs/type-safety/invalid_transition.py \
  docs/type-safety/incomplete_match.py docs/type-safety/wrong_currency.py
```

Очікувано: три помилки, наведені вище. Після цього:

```bash
py -3 check.py
```

Очікувано: `Success: no issues found in 22 source files` і `143 passed`.
